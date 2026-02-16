# BuzzBot Evaluation Metrics

## Retrieval
- Precision@K: relevant contexts in top K
- Coverage: % questions with at least 1 relevant chunk
- Freshness coverage: for sensitive queries, % where retrieved chunk fetched_at within target window

## Generation
- Citation coverage: % answers with >=1 citation for each factual claim (approx via heuristic)
- Faithfulness: % answers with no unsupported claims (use grounding_check fail rate)
- Policy compliance: no prohibited scraping guidance, no misrepresentation of unofficial sources

## Targets (suggested)
- Coverage@5 >= 0.85
- Grounding_check pass rate >= 0.90
- Freshness correctness >= 0.95 for deadline queries
