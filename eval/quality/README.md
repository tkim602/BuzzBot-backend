# BuzzBot deterministic retrieval quality eval

This is the local, deterministic benchmark for BuzzBot's official-document RAG. It does not use LangSmith or an LLM judge.

## One-command run

```bash
make quality-eval
```

The command evaluates the fixed 1,000-query verified dataset in `eval/quality/data_verified` against the current local PostgreSQL + pgvector corpus and writes:

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
