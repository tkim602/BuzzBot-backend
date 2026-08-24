# Policy Hierarchical Retrieval Experiment Design

## Goal

Determine whether Policy decisive-evidence misses are primarily caused by global document competition or by weak chunk representation/ranking inside the correct document.

## Scope

The experiment runs the fixed Policy dev-100 manifest in two modes using one shared query embedding per case:

1. `global`: the current production Policy retrieval path.
2. `oracle_document`: chunk retrieval restricted to the committed gold-evidence URL.

The oracle path reuses existing vector similarity, lexical match scoring, reciprocal-rank fusion, and cross-encoder reranking. It is evaluation-only and does not alter production retrieval, routing, thresholds, final top-k, frozen fixtures, ingestion, or answer validation.

## Data flow

For each case, load the committed `GoldEvidence` URL, retrieve that document's indexed chunks, rank the chunks with existing signals, and compute the unchanged exact evidence-span metric at ranks 1, 3, and 5. Compare those results with the current global production result. Group the previously unresolved PR11 cases by their committed root-cause taxonomy.

## Outputs

The run writes a machine-readable summary, per-case JSONL, and a concise Markdown decision report containing global and oracle Evidence Hit@1/3/5, oracle MRR@5, mean/p95 latency, and unresolved-category results.

The architectural decision is:

- oracle Evidence Hit@5 >= 90%: hierarchical retrieval is supported for a later evaluation-only prototype;
- otherwise: do not build hierarchical production retrieval; investigate representation and within-document ranking.

No paid semantic answer evaluation runs in PR12.

## Failure handling

Missing gold documents, missing spans, or documents without indexed chunks remain explicit misses. The evaluator fails on malformed manifests, evidence artifacts, or PR11 taxonomy artifacts rather than silently changing the denominator.

## Verification

Unit tests cover URL-conditioned candidate selection, metric aggregation, taxonomy grouping, and report rendering. The fixed dev-100 run is executed once, followed by the existing unit, lint, database integration, Schedule, Course Details, and Calendar regression gates.
