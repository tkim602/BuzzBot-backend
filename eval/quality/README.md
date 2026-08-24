# BuzzBot quality evaluation

The retrieval benchmark is local and deterministic. The chat benchmark calls the real `/v2/chat` production contract and judges responses with the configured `gpt-4o-mini`; both production and judge calls share the existing $3 usage guard.

## Commands

```bash
# Fast retrieval loop; uses embeddings but no chat completion
make quality-retrieval-dev

# Material-change retrieval gate
make quality-retrieval-change

# Full retrieval release gate only
make quality-retrieval-full

# PR12 architecture experiment: production vs. gold-document-conditioned chunks
# Uses query embeddings and local retrieval/reranking; makes no answer/judge calls.
make quality-policy-oracle

# In another terminal, start the API before a live chat evaluation
make run-backend

# Actual gpt-4o-mini + /v2/chat evaluation; resumes automatically
make quality-chat-dev

# Only after a material change
make quality-chat-change

# Offline A/B/C diagnosis; reads the current DB and existing dev-100 reports
make quality-diagnose-dev
```

The fixed 100- and 200-case manifests select from the unchanged 1,000-query verified dataset in `eval/quality/data_verified`. There is intentionally no `quality-chat-full` target.

Retrieval reports are written beneath `eval/quality/reports_retrieval_*`; chat reports beneath `eval/quality/reports_chat_*`.

`quality-diagnose-dev` makes no network/model calls. It separates missing indexed evidence (A), retrieval misses (B), and post-retrieval failures (C). Pass `retrieval_report=... chat_report=...` when the frozen baseline reports live outside the checkout.

Chat evaluation is sequential and resumable. Only `COMPLETED` case IDs are skipped; failed or budget-stopped cases retry on the next run. Production chat and judge token/cost deltas are attributed per case from the shared usage history.

All current gold cases are answerable, so abstention is a failure and `correct_abstention_rate` remains `null` until a separate unanswerable benchmark is approved. The first baseline also leaves `confidence_threshold` and `unsafe_confident_answer_rate` as `null`; raw case confidence and p50/p95 values are retained for a later reviewed threshold.

The full retrieval command writes:

- `eval/quality/reports/latest_summary.md`
- `eval/quality/reports/latest_summary.json`
- `eval/quality/reports/latest_cases.jsonl`
- `eval/quality/reports/latest_failures.jsonl`

## Metrics

- **Doc Hit@1** — gold document is first; measures immediate evidence precision.
- **Doc Hit@3** — gold document appears in the top 3; measures retrieval with a small evidence budget.
- **Doc Hit@5** — gold document appears in the top 5; primary retrieval quality metric.
- **MRR@5** — rewards earlier gold ranks; distinguishes rank 1 from rank 5.
- **Source Hit@1/5** — expected Georgia Tech authority appears early; detects wrong-source retrieval.
- **Vertical Hit@1** — top result is in the expected institutional vertical; detects domain confusion.
- **Empty Retrieval Rate** — no evidence was returned; catches filter/threshold failures.
- **Fact Macro Hit@5** — averages performance over the 100 facts rather than over paraphrases.
- **All Variants Hit@5** — every phrasing for a fact succeeds; measures robustness to user wording.
- **p50/p95 latency** — typical and tail retrieval latency, excluding the one-time batched embedding step.
- **Gold Corpus Coverage** — gold evidence is actually present in the current DB; separates ingestion gaps from ranking quality.

## Views in one run

1. `production`: current graph understanding + production document retrieval wrappers.
2. `raw`: unfiltered hybrid retrieval over the entire document corpus; diagnoses routing/filter loss.
3. `vector`: pgvector only.
4. `fts`: PostgreSQL full-text search only.

`raw` is the unfiltered hybrid baseline, so the ablation compares `vector`, `fts`, and `raw/hybrid` without running the identical hybrid query twice.

The first run is a baseline. It does not fail based on arbitrary quality thresholds. Quality gates should be frozen only after the baseline is reviewed.
