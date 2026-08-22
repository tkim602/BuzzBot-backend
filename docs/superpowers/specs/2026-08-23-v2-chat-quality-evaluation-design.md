# BuzzBot `/v2/chat` Quality Evaluation Design

## Goal

Measure whether the production LangGraph chatbot is reliable enough for students without rerunning the full 1,000-query benchmark after every change.

The legacy frontend and `POST /chat` endpoint are outside this evaluation. `POST /v2/chat` is the production contract under test.

## Existing assets

- Keep `eval/quality/data_verified/gold_v1_500.json` and `gold_v2_500.json` unchanged as the 1,000-query master benchmark.
- The master benchmark contains 100 web-verified Georgia Tech facts with ten question variants per fact.
- Reuse the existing gold answers, official URLs, source labels, verticals, question types, and time-sensitive flags.
- Reuse the current LangGraph answer generation, citation grounding, claim validation, usage accounting, and `$3` hard limit.

## Evaluation tiers

### Development gate: 100 fixed cases

Maintain an explicit, versioned 100-case manifest containing 100 concrete case IDs, with exactly one question for each of the 100 fact groups. Prefer a previously failing or difficult realistic phrasing; otherwise use a natural phrasing. The selection remains fixed between runs so results are comparable.

Run this tier while improving retrieval, routing, or answer behavior.

### Change gate: 200 fixed cases

Maintain an explicit 200-case manifest with two fixed variants per fact. It must be a strict superset of the 100-case manifest so every development regression remains present in the change gate. Run this only after a material retrieval, routing, prompt, or graph change. This tier is not required for small test-only or documentation changes.

### Release gate: 1,000 cases

Run the existing full verified benchmark only before merge/release or when producing portfolio metrics. Never make it the inner development loop.

## Two complementary evaluations

### Retrieval evaluation

Reuse `eval.quality.runner`. It measures whether the verified official document appears in the ranked results and keeps the existing production/raw/vector/FTS diagnostic views. It does not call the chat-completion model.

### End-to-end chat evaluation

Invoke the same LangGraph path used by `POST /v2/chat`. With the current environment this uses `gpt-4o-mini` for answer generation and for existing semantic checks when required.

For each case, store:

- case ID, question, gold answer, and gold URLs;
- final answer, citations, confidence, notes, and abstention status;
- latency and recorded token/cost delta;
- deterministic citation checks;
- a strict structured correctness judgment against only the gold answer and official retrieved/cited evidence.

The correctness judge uses the already configured model and returns a small fixed JSON result. It is evaluation-only and does not change production behavior.

## Execution semantics

- Process cases with small bounded concurrency.
- Append one JSONL result per completed case.
- On restart, skip completed case IDs unless `--force` is supplied.
- Stop cleanly when the existing usage limiter rejects further API calls.
- If chat is rejected by the budget guard, record the current case without an answer and stop.
- If chat succeeds but the judge is rejected by the budget guard, preserve the production answer and citations with a null judgment, record the case, and stop.
- For other HTTP or judge errors, record a failed case and continue.
- Do not expose API keys or copy `.env` values into reports.
- Do not retry failed cases indefinitely; record the failure and continue unless the budget guard stops the run.

## Metrics

Report separately:

- answer correctness;
- supported/cited answer rate;
- correct abstention rate;
- unsafe confident-answer rate;
- citation URL hit rate against the verified gold URLs;
- latency and estimated API cost;
- breakdowns by vertical, question type, difficulty, and time sensitivity.

Retrieval metrics remain separate from answer metrics. A retrieved gold document is not counted as a correct final answer.

## Initial quality policy

The system remains fail-closed: a supported abstention is preferable to an unsupported confident answer. Initial runs establish the baseline. Release thresholds are frozen only after reviewing the 100-case end-to-end report, rather than inventing thresholds before observing the production path.

## Scope exclusions

- No frontend work.
- No changes to the legacy `POST /chat` endpoint.
- No ingestion, crawler, OSCAR, chunking, or source-scope changes.
- No new model, evaluation service, vector database, or framework.
- No automatic full 1,000-case chat run during implementation.

## Verification

Implementation is complete when:

1. the fixed 100-case manifest contains one explicit case ID from every fact group with no duplicates;
2. the 200-case manifest contains two explicit case IDs from every fact group with no duplicates and contains every 100-case ID;
3. mocked focused tests separately verify chat budget rejection, judge budget rejection, ordinary errors, result recording, resume, metrics, and shared usage accounting;
4. a bounded real smoke run of at most two cases reaches the `/v2/chat` LangGraph path;
5. the full existing test suite and lint pass;
6. the 1,000-case live chat evaluation is not run automatically.
