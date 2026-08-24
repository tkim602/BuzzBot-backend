# Policy Hierarchical Retrieval Prototype Design

## Goal

Test whether a non-oracle, evaluation-only two-stage Policy retrieval pipeline can raise decisive Evidence Hit@5 from the current 70% baseline to at least 85% without changing production behavior.

## Architecture

For every fixed Policy dev-100 question:

1. Stage 1 uses the existing routed, document-diverse hybrid retrieval path to rank official documents. It retrieves five documents once and compares the fixed prefixes `N = 1, 2, 3, 5`.
2. Stage 2 restricts chunk candidates to the selected documents. Within each document, it reuses the PR12 vector, lexical, and reciprocal-rank-fusion signals to retain at most 15 candidates.
3. Cross-document merge uses the existing cross-encoder once over the bounded candidate set and returns the existing final top five.

Gold URLs and decisive spans are used only after retrieval for scoring. They never influence Stage 1, Stage 2, or merge selection.

## Comparison

For each `N`, report document recall, Evidence Hit@1/3/5, Evidence MRR@5, mean latency, and p95 latency. The minimum candidate gate is Evidence Hit@5 >= 85%; 90% is the stretch gate. The report selects the smallest `N` tied for the best passing Hit@5, using MRR and latency as secondary evidence rather than sweeping additional parameters.

## Constraints

This is an evaluation path only. It does not modify production retrieval, routing, thresholds, top-k, ingestion, chunking, answer validation, PR10 fixtures, or the eight PR12 oracle misses. It adds no model and performs no paid answer evaluation.

## Failure handling

Missing selected documents or indexed chunks remain retrieval misses. The runner validates exactly 100 manifest cases and only the approved `N` values. Errors are reported rather than changing the denominator.

## Exit

If no `N` reaches 85%, close the hierarchical hypothesis without production work. If a candidate reaches 85%, record it as a production candidate for a later PR; do not switch production in PR13.
