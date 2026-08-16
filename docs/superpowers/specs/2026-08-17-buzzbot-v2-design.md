# BuzzBot v2 — Production-Oriented Controlled Agentic RAG Design

**Date:** 2026-08-17

**Status:** Final approved design

**Primary goal:** Build a read-only Georgia Tech student information assistant that answers from verified, versioned evidence and can be deployed as a portfolio-quality public service.

## 1. Scope

BuzzBot v2 answers questions about:

- Georgia Tech policies, academic calendars, registration dates, and student-facing procedures
- course descriptions, credits, prerequisites, and program requirements
- term-specific offerings, sections, CRNs, instructors, meeting times, and locations
- mixed planning questions that combine program requirements with actual term offerings
- multi-turn follow-up questions

It does not register or drop courses, use a student's GT credentials, or expose personalized holds, time tickets, waitlist positions, or schedules.

Current-seat answers are excluded until a separate public-access validation proves that the data is available without authentication and can be collected reliably and responsibly.

## 2. Design Principles

1. **Data quality before chat quality.** Build collection, normalization, versioning, and validation first. Tune prompts and answer style later.
2. **Official evidence first.** Public OSCAR and official GT documents are authoritative. GT Scheduler is initially a reference and parity dataset, not a silently merged source of truth.
3. **Probe before bulk collection.** Every provider must pass a small bounded probe before any full synchronization begins.
4. **SQL for facts, RAG for semantics.** Structured schedule facts are relational data, not embedding chunks.
5. **Controlled LangGraph workflow.** Deterministic routing and bounded retries replace free-form ReAct loops and multi-agent orchestration.
6. **Observable evidence, not model confidence.** Responses report evidence status, source type, and data timestamp.
7. **Optimize measured bottlenecks.** Keep module boundaries clear, reuse existing dependencies, and avoid speculative abstractions or infrastructure.

## 3. Claim-Specific Authority and Failure Policy

Authority is selected by claim type rather than by one global source ranking.

| Claim type | Primary authority | Fallback behavior |
| --- | --- | --- |
| Term offering, CRN, section, instructor, time, location | Current public OSCAR | Last-known-good OSCAR snapshot within its maximum age |
| Course description, credits, prerequisites | GT Catalog, with OSCAR details where term-specific | Cite the available official source and identify any conflict |
| Degree and specialization requirements | Applicable GT Catalog edition | Official program documentation for program-specific clarification |
| Registration policy and procedures | Registrar | Abstain and link to Registrar if evidence is unavailable |
| Academic dates and deadlines | Official Academic Calendar | No third-party fallback for exact deadlines |
| OMSCS-specific rules | Official OMSCS/GT documentation | Catalog or Registrar only for the portion they govern |
| Comparison and ingestion debugging | GT Scheduler | Never silently merge it into official evidence |

Public OSCAR is the primary source of truth for term-specific schedule claims, but its current HTML routes are not treated as a stable official API contract. Provider code must therefore isolate page/session details behind one OSCAR adapter and retain raw snapshots for replay and parser regression tests.

The system must never replace a valid published dataset with a failed, partial, empty, or schema-incompatible collection.

Every dataset has a configured freshness target and hard maximum age. Internal freshness states are deliberately limited to:

- `CURRENT`: within the freshness target
- `STALE`: older than the target but within the hard maximum; answers must include `data_as_of` and a warning
- `EXPIRED`: older than the hard maximum; volatile claims cannot be answered from the snapshot

For current/future-term OSCAR schedules, the initial defaults are a 6-hour freshness target and a 24-hour hard maximum. Historical completed-term schedules do not expire for historical claims, but still retain `data_as_of`. Official-document freshness is configured per source because calendars, catalogs, and policy pages change at different rates.

## 4. Probe-First Collection

Every collection run begins with a provider probe. A probe is a real but deliberately small sample, not a complete preflight crawl.

Default probe budget:

- at most 5 HTTP requests per provider
- at most 20 parsed records
- one representative term and subject/course
- 15-second request timeout
- concurrency of 2 across providers
- one retry only for transient 5xx or connection errors
- stop immediately on authentication redirects, any 429 response, forbidden access, or incompatible markup

Providers may define a smaller budget or an explicitly reviewed larger budget when their public navigation handshake requires it. Budget changes are configuration, must remain bounded, and must not be inferred dynamically by crawling more URLs.

An HTTP 429 is not retried during a probe. The probe records `RATE_LIMITED`, captures `Retry-After` when present, prohibits bulk synchronization, and leaves retry scheduling to a later run.

The OSCAR probe should verify one known term/course listing and, where public, its detail page. Document-source probes should fetch only the configured root and one representative content page.

Each probe returns a machine-readable report:

```json
{
  "provider": "public_oscar",
  "reachable": true,
  "public_access": true,
  "parsed_records": 3,
  "required_fields_present": true,
  "latency_ms": 420,
  "status": "READY",
  "reason": null,
  "retry_after_seconds": null
}
```

Bulk synchronization is allowed only when the current probe status is `READY`. A failed provider is marked unavailable and skipped; the runner does not keep trying adjacent URLs to discover whether some subset works.

Supported execution modes:

- `probe --source <name>`: bounded readiness check only
- `sync --source <name>`: probe, then synchronize only on success
- `sync-all`: low-concurrency probes followed by synchronization of ready providers

No LLM is used for probing, crawling, parsing, normalization, or schema validation.

## 5. Ingestion Pipelines

### 5.1 Structured schedule data

```text
Public OSCAR
  -> bounded probe
  -> raw response snapshot
  -> deterministic parser
  -> typed normalized records
  -> staging tables
  -> schema, integrity, coverage, and freshness gates
  -> atomic publish of a new data version
```

Raw snapshots persist only the response body, status code, content type, fetch timestamp, source URL, and SHA-256 hash. They must not persist `Set-Cookie`, session identifiers, authorization headers, GT credentials, CSRF values, or other transient request tokens.

Required publish gates:

- expected term is present
- at least 99% of fetched records parse successfully
- `(term_code, crn)` is unique
- every section references a course
- every meeting references a section
- required section fields are present or explicitly marked TBA
- requested term and subject coverage matches the collection plan
- parsed counts match source-reported totals when available
- an unexplained large drop from the last-known-good snapshot fails publication or requires explicit review
- source URL, fetch timestamp, snapshot hash, parser version, and data version are recorded

If any gate fails, the staged version is rejected and the last-known-good version remains active.

### 5.2 Official document data

```text
Curated source registry
  -> bounded probe
  -> fail-closed access-policy check
  -> conditional fetch using ETag/Last-Modified where available
  -> source-specific extraction
  -> content hash comparison
  -> changed-document chunking
  -> embedding of changed chunks only
  -> quality gates
  -> atomic publish
```

The production path excludes the existing broad `gt-all` crawl. Required official sources are configured explicitly. A 429 response pauses that provider instead of increasing retries or parallelism.

## 6. Minimal Data Model

Structured data:

- `academic_terms`: term code, display name, source timestamps, active data version
- `courses`: subject, number, title, description, credits, prerequisites JSON
- `sections`: term, CRN, course, section code, campus, schedule type, instructional method, notes
- `meetings`: section, meeting type, days, start/end time, date range, building, room
- `section_instructors`: section, instructor name, primary flag
- `source_snapshots`: provider, source URL, fetched time, content hash, parser version, raw location, validation status
- `data_versions`: provider, version, publish status, row counts, published time

Document RAG retains documents, chunks, and pgvector embeddings, with these corrections:

- source ownership is explicit and cannot silently change through URL collisions
- empty titles or bodies fail validation
- every chunk retains the canonical URL and collection timestamp
- only changed content is re-embedded
- schedule rows are never represented primarily as embedding chunks

Seat availability, if later validated, uses a short-lived cache separate from durable section facts.

## 7. LangGraph Workflow

```text
Student question
  -> Understand query
  -> Determine required evidence
  -> Retrieve in parallel where useful
       - document Hybrid RAG
       - structured SQL lookup
       - optional validated public fresh lookup
  -> Validate evidence
       - deterministic checks for structured rows
       - retrieval metadata/score and optional LLM relevance check for documents
  -> Rewrite/retrieve at most once when evidence is missing
  -> Synthesize answer
  -> Validate claims and citations
  -> Answer or abstain
```

Routing is deterministic when entities and intent are clear. For example, `term + course code + schedule intent` routes directly to SQL. An LLM decomposition step is reserved for ambiguous or genuinely mixed questions.

The graph has no unbounded tool loop, multi-agent system, or free-form ReAct planner.

### State

The typed graph state contains:

- conversation messages and thread identifier
- normalized query and intent
- extracted term, course, instructor, campus, and time constraints
- required evidence types
- structured rows and document evidence
- source versions and timestamps
- retry count, validation errors, answer, and citations

Formatted prompts and unrestricted database results are not persisted in graph state.

### Evidence status

- `VERIFIED`: all required claims have valid evidence
- `PARTIAL`: only part of the question is supported
- `STALE`: a last-known-good snapshot is outside its freshness target but still within its hard maximum
- `INSUFFICIENT`: the system must abstain or ask a clarifying question

An expired volatile dataset produces `INSUFFICIENT`, not `STALE`. The model does not assign its own `high/medium/low` confidence label.

## 8. Module and Dependency Boundaries

Use a small number of cohesive modules rather than a file per class.

```text
app/
  api/                 HTTP/SSE boundary only
  graph/               state, nodes, conditional edges, graph construction
  retrieval/           document search and structured schedule queries
  llm/                 LangChain model construction and bounded prompts
  core/                configuration, database lifecycle, logging, usage accounting

ingestion/
  providers/           OSCAR and official-document HTTP/session adapters
  schedule/            schedule parser, validation, normalization, synchronization
  documents/           document extraction, change detection, chunking, indexing
  runner.py            probe/sync command orchestration
```

Dependency direction:

```text
API -> graph -> retrieval/LLM -> database or provider
runner -> provider -> parser/validator -> database
```

Lower layers never import API or graph modules. Provider code performs transport only; parsers accept saved responses so they can be tested without network access. Database constraints enforce uniqueness and referential integrity rather than duplicating those rules in every caller.

Performance work is evidence-driven. Initial budgets and traces cover request count, bytes fetched, rows parsed, changed documents, embedding tokens, LLM input/output tokens, graph node latency, and total answer latency. Redis, Kafka, n8n, and additional services are excluded until measured load requires them.

## 9. Cost-Constrained Model Policy

Development defaults:

- chat and structured output: `gpt-4o-mini`
- embeddings: `text-embedding-3-small`
- optional local/offline model: Ollama
- LangSmith tracing: disabled by default

Quality-evaluation candidate:

- `gpt-5.6-luna` with `reasoning.effort` set to `none` or `low`

As of 2026-08-17, official OpenAI documentation lists:

- `gpt-4o-mini`: $0.15 input / $0.60 output per 1M tokens
- `gpt-5.6-luna`: $0.20 input / $1.20 output per 1M tokens
- `text-embedding-3-small`: $0.02 per 1M embedding tokens

Model IDs and prices are configuration, not hard-coded business logic, and must be rechecked before public launch.

Development token controls:

- deterministic query parsing before any LLM classification
- no LLM calls in ingestion
- one answer-generation call for a normal request
- document relevance grader only for ambiguous retrieval
- at most one query rewrite
- bounded retrieved chunks and per-chunk length
- short answer output limit during development
- record tokens and estimated cost for every model call
- no chat-quality prompt experiments until collection and architecture acceptance tests pass

## 10. Credentials

No API key is required to implement and test OSCAR/document probing, parsing, normalization, database schemas, or the LangGraph control flow with mocked models.

Before real embedding or chat integration, the user must provide locally through environment configuration:

- `OPENAI_API_KEY`: required for OpenAI chat and embeddings
- `LANGSMITH_API_KEY`: optional, only if hosted tracing/evaluation is enabled

The application must not log, commit, or return keys. The key is placed by the user in a local `.env` or deployment secret manager, never sent in chat. Public OSCAR collection must not use GT credentials.

## 11. Deployment

Minimal deployment services:

- Next.js frontend
- FastAPI + LangGraph API with SSE
- PostgreSQL + pgvector + LangGraph PostgreSQL checkpointer
- scheduled one-shot ingestion job

Local development uses Docker Compose. Public deployment uses the same containers and a persistent PostgreSQL service. `/live` checks the process; `/ready` checks database connectivity, active data versions, and freshness. Administrative usage-limit endpoints must be removed or protected before exposure.

## 12. Evaluation and Acceptance

Data acceptance:

- probe prevents bulk execution for inaccessible or incompatible providers
- one 429 marks the provider `RATE_LIMITED` without an immediate retry
- snapshots contain no cookies, session identifiers, credentials, or transient request tokens
- parser success at least 99% for accepted schedule snapshots
- zero duplicate `(term_code, crn)` rows
- 100% section-to-course referential integrity
- collected term/subject coverage and record-count envelopes are validated before publication
- no partial batch can replace the last-known-good version
- changed-only document embedding is demonstrated

Retrieval and answer acceptance after data architecture is complete:

- structured schedule field accuracy at least 98%
- document Recall@5 at least 85%
- factual citation coverage at least 95%
- unsupported factual claim rate at most 2%
- correct abstention on unsupported questions at least 90%
- one retrieval rewrite maximum

Evaluation categories include policy, calendar, course details, prerequisites, term offerings, section facts, mixed program/schedule constraints, follow-ups, stale data, and unanswerable questions. The before/after portfolio report must compare the existing generic-crawl/all-embedding pipeline with the new source-specific, normalized, validated pipeline using the same held-out questions.

## 13. Implementation Order

1. Phase 0: bounded OSCAR and official-source probes; record feasibility results
2. Structured schedule schema, parser, snapshots, validation, and atomic publishing
3. Curated changed-only document ingestion and indexing
4. Repository/query layer and deterministic retrieval tests
5. Controlled LangGraph workflow using mocks, then cheap-model integration
6. Citation/claim validation and evaluation harness
7. SSE API/frontend integration and Docker/public deployment hardening
8. Prompt and model quality tuning only after previous phases pass

## References

- [OSCAR](https://oscar.gatech.edu/)
- [Georgia Tech Schedule of Classes](https://registrar.gatech.edu/current-students/schedule-of-classes)
- [Georgia Tech Catalog](https://catalog.gatech.edu/)
- [Georgia Tech Academic Calendar](https://registrar.gatech.edu/calendar)
- [Georgia Tech Acceptable Use Policy](https://policylibrary.gatech.edu/information-technology/acceptable-use-policy)
- [GT Scheduler crawler-v2](https://github.com/gt-scheduler/crawler-v2)
- [LangGraph Agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [OpenAI GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [OpenAI GPT-4o mini](https://developers.openai.com/api/docs/models/gpt-4o-mini)
- [OpenAI text-embedding-3-small](https://developers.openai.com/api/docs/models/text-embedding-3-small)
