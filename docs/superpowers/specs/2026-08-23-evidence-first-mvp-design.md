# BuzzBot Evidence-First MVP Design

## Goal

Raise BuzzBot from a technically sound portfolio system to a usable, trust-first Georgia Tech chatbot without replacing the current LangGraph, structured schedule retrieval, document adapters, models, or safety validators.

## Current Baseline

All comparisons use the fixed `dev_100` manifest and the same database snapshot.

| Metric | Before retrieval fix | Current |
|---|---:|---:|
| Retrieval Hit@5 | 42% | 57% |
| Retrieval MRR@5 | 0.297 | 0.400 |
| Answer correctness | 27% | 33% |
| Evidence support | 29% | 34% |
| Gold citation hit | 20% | 23% |
| Abstention | 65% | 60% |

The `42% → 57%` change is explained by the calendar-routing and document-diversification commits: 17 retrieval wins and 2 regressions. It is not evaluation drift.

The current case-level funnel is:

```text
100 questions
├─ gold URL retrieved in top 5: 57
│  ├─ correct and supported: 24
│  └─ post-retrieval loss: 33
└─ gold URL not retrieved in top 5: 43
   ├─ correct and supported from another source: 9
   └─ retrieval miss and bad answer: 34
```

URL-level Hit@5 is therefore insufficient: it does not prove that the answer-supporting chunk from that URL reached the answerer.

## MVP Release Targets

These are target gates, not claims about the next single patch.

- Evidence Hit@5: at least 70%
- Supported answer correctness: at least 55%, with 60% as the preferred target
- Supporting citation hit: at least 55%
- Abstention: at most 40%
- Unsafe answered rate: zero (a non-abstained answer judged incorrect or unsupported)
- Chat p95 latency: at most 10 seconds
- No regression to structured course, section, or schedule retrieval

## Architecture

```text
Question
  ↓
Existing intent routing
  ├─ course / section / schedule → existing structured SQL
  └─ policy / document
         ↓
     existing hybrid candidate generation
         ↓
     document-diverse candidates with relevant sibling chunks
         ↓
     evidence span selection
         ↓
     existing answer generation
         ↓
     existing claim and citation validation
```

No new agent, model, crawler, vector database, or generic provider abstraction is introduced.

## Phase 1: Make Evaluation Truthful

Before tuning chunks, correct the evaluator vocabulary and add an evidence-level metric.

1. Replace the misleading `ALL_METHODS_FAIL` tag. The current implementation duplicates `raw` and excludes production. The replacement must distinguish:
   - all ablation channels miss;
   - production misses;
   - production recovers an ablation miss.
2. Keep URL Hit@5 for continuity.
3. Add one exact, source-derived supporting evidence span for each of the 100 fixed facts. The evidence text is a committed evaluation artifact, not generated during a run.
4. Add Evidence Hit@K: a hit requires a retrieved chunk to contain the normalized supporting span. Exact source quotes make this deterministic and avoid another evaluator model call.
5. Keep the existing chat judge and usage accounting unchanged.

This phase changes evaluation only, not production answers.

## Phase 2: Preserve Local Section Context

The current chunker is lossless, but indexing weakens local meaning:

- later token windows can lose the section heading from their embedded text;
- every chunk receives the entire document heading list, which adds unrelated lexical noise;
- URL-level diversification may keep the wrong chunk from the correct document.

The minimum correction is:

1. Keep the existing 500-token size and 80-token overlap initially. Do not run a chunk-size sweep.
2. Store the chunk's own `section_heading` / `page_section_path` in the chunk heading field instead of the full document heading list.
3. Embed contextual text composed from document title, local section path, and raw chunk text.
4. Preserve raw `chunk_text` unchanged for exact citation grounding.
5. Keep short-section merging and table relationship serialization lossless.
6. Increment `CHUNKING_VERSION` and use the existing explicit safe reindex path. Unchanged trusted documents are reindexed only when their stored version is older.

## Phase 3: Retrieve Documents and Evidence, Not Duplicate Chunks

Reuse the existing vector, FTS, RRF, and cross-encoder stack.

1. Keep bounded candidate generation and source filters.
2. Limit duplicate chunks per document before fusion, but allow up to two candidates from a document so the answer-supporting sibling is not discarded.
3. Rerank using title, local section path, and chunk body.
4. Apply final document diversity after reranking while allowing a second chunk only when it is among the strongest evidence candidates.
5. Pass those selected chunks directly to the existing answerer and claim validator.

No query rewrite, multi-query retrieval, new reranker, embedding replacement, or source-specific answer rule is allowed in this phase.

## Phase 4: Answer Utilization

Only after Evidence Hit@5 improves:

1. Inspect cases where supporting evidence reaches the answerer but the answer abstains or fails validation.
2. Reuse the existing claim-to-evidence selector and semantic verifier.
3. Make the smallest evidence-handoff or answer instruction correction supported by the largest remaining failure bucket.
4. Preserve cite-or-abstain and fail-closed validation.

This phase must not compensate for missing evidence with a looser validator.

## Verification Sequence

Each production change follows:

```text
failing focused test
  ↓
minimal implementation
  ↓
focused tests
  ↓
full tests + DB integration + lint
  ↓
retrieval dev-100
  ↓
case-level wins/regressions and evidence metrics
```

Chat dev-100 runs only after a retrieval/evidence milestone passes. Change-200 and full-1000 chat evaluations remain out of scope until the MVP gates are close enough to justify their cost.

## Stop Conditions

- Reject a chunk/index change if Evidence Hit@5 does not improve meaningfully or URL Hit@5/MRR materially regress.
- Do not stack another heuristic on a failed experiment.
- Do not lower grounding or semantic-validation strictness to raise answer rate.
- Do not expand the corpus during this iteration; corpus coverage is already 100% for dev-100.
- Preserve all existing trusted documents if reindexing fails.

## Scope

Included:

- evaluation taxonomy correction;
- fixed evidence spans for dev-100;
- local section context in chunk indexing;
- bounded relevant sibling-chunk retrieval;
- evidence handoff improvements proven by case-level evaluation.

Excluded:

- frontend work;
- ingestion scope expansion;
- OSCAR changes;
- new models or agents;
- personalization/authentication;
- prompt tuning before evidence retrieval is verified;
- full live evaluation.
