# BuzzBot PR11 Policy evidence-miss diagnosis

- Generated: 2026-08-24T08:34:38.413191+00:00
- Scope: exactly the 30 frozen PR10 Policy evidence misses
- Cases: 30

## Root-cause distribution

| Root cause | Cases |
|---|---:|
| CANDIDATE_GENERATION_TRUNCATION | 5 |
| CHILD_RESELECTION_LOSS | 4 |
| CHUNKING_BOUNDARY_LOSS | 3 |
| DOCUMENT_RETRIEVED_WRONG_CHUNK | 8 |
| FUSION_OR_RERANK_LOSS | 5 |
| GOLD_OR_EVAL_DEFINITION_ISSUE | 3 |
| RESOLVED_SINCE_FREEZE | 2 |

## Case diagnosis

| Case | Root cause | Earliest failed stage | Candidate ranks (production pool / pre-rerank / deep vector / deep FTS OR) |
|---|---|---|---|
| gold-004-v10 | DOCUMENT_RETRIEVED_WRONG_CHUNK | chunk_selection | 5 / 8 / 5 / 5 |
| gold-006-v7 | CHILD_RESELECTION_LOSS | child_reselection | 3 / 5 / 43 / 14 |
| gold-007-v10 | DOCUMENT_RETRIEVED_WRONG_CHUNK | chunk_selection | None / None / 25 / 16 |
| gold-009-v10 | CANDIDATE_GENERATION_TRUNCATION | candidate_generation | None / None / 195 / None |
| gold-010-v10 | CANDIDATE_GENERATION_TRUNCATION | candidate_generation | None / None / 74 / None |
| gold-011-v10 | CANDIDATE_GENERATION_TRUNCATION | candidate_generation | None / None / 101 / 33 |
| gold-012-v2 | CANDIDATE_GENERATION_TRUNCATION | candidate_generation | None / None / 7 / 1 |
| gold-013-v8 | FUSION_OR_RERANK_LOSS | fusion_rerank_top_k | 2 / 8 / 2 / 169 |
| gold-014-v8 | FUSION_OR_RERANK_LOSS | fusion_rerank_top_k | 1 / 1 / 1 / None |
| gold-024-v3 | CHUNKING_BOUNDARY_LOSS | chunk_availability | None / None / None / None |
| gold-030-v10 | CHUNKING_BOUNDARY_LOSS | chunk_availability | None / None / None / None |
| gold-039-v10 | CHILD_RESELECTION_LOSS | child_reselection | 5 / 8 / 12 / 10 |
| gold-040-v10 | GOLD_OR_EVAL_DEFINITION_ISSUE | gold_definition | None / None / None / None |
| gold-049-v10 | GOLD_OR_EVAL_DEFINITION_ISSUE | gold_definition | None / None / None / None |
| gold-050-v10 | FUSION_OR_RERANK_LOSS | fusion_rerank_top_k | 5 / 5 / 25 / 16 |
| gold-051-v7 | CHILD_RESELECTION_LOSS | child_reselection | 3 / 2 / 3 / 12 |
| gold-054-v10 | FUSION_OR_RERANK_LOSS | fusion_rerank_top_k | 3 / 4 / 3 / 13 |
| gold-060-v3 | FUSION_OR_RERANK_LOSS | fusion_rerank_top_k | 6 / 7 / 53 / 81 |
| gold-062-v9 | CHILD_RESELECTION_LOSS | child_reselection | 1 / 1 / 1 / 1 |
| gold-063-v7 | DOCUMENT_RETRIEVED_WRONG_CHUNK | chunk_selection | None / None / 29 / 1 |
| gold-064-v9 | RESOLVED_SINCE_FREEZE | resolved | None / None / 8 / 2 |
| gold-067-v10 | RESOLVED_SINCE_FREEZE | resolved | 2 / 2 / 7 / 6 |
| gold-071-v10 | CHUNKING_BOUNDARY_LOSS | chunk_availability | None / None / None / None |
| gold-072-v10 | GOLD_OR_EVAL_DEFINITION_ISSUE | gold_definition | None / None / None / None |
| gold-073-v10 | DOCUMENT_RETRIEVED_WRONG_CHUNK | chunk_selection | None / None / 32 / 27 |
| gold-079-v7 | DOCUMENT_RETRIEVED_WRONG_CHUNK | chunk_selection | None / None / 9 / 8 |
| gold-083-v7 | DOCUMENT_RETRIEVED_WRONG_CHUNK | chunk_selection | 5 / 7 / 5 / 42 |
| gold-088-v4 | CANDIDATE_GENERATION_TRUNCATION | candidate_generation | None / None / 6 / 28 |
| gold-091-v3 | DOCUMENT_RETRIEVED_WRONG_CHUNK | chunk_selection | None / None / 12 / 8 |
| gold-092-v10 | DOCUMENT_RETRIEVED_WRONG_CHUNK | chunk_selection | None / None / 19 / 10 |

`document_hit_at_5` means a gold URL is present in the first five results. `evidence_hit_at_5` additionally requires the committed decisive span to occur in a returned chunk from that URL.
