# BuzzBot PR12 document-conditioned Policy retrieval

- Cases: 100
- Final top-k: 5
- Paid semantic answer evaluation: not run

## Headline result

| Mode | Evidence Hit@1 | Hit@3 | Hit@5 | MRR@5 | Mean ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| Global production | 39.0% | 60.0% | 70.0% | 0.506 | 2276.1 | 3902.3 |
| Oracle document | 64.0% | 83.0% | 92.0% | 0.748 | 698.8 | 1800.8 |

## Decision

- Gate: Oracle Evidence Hit@5 >= 90%
- Result: `HIERARCHICAL_RETRIEVAL_SUPPORTED`
- Lift over global production: **+22 percentage points**

The experiment supports a later evaluation-only hierarchical prototype: most decisive
Policy evidence is rankable once global document competition is removed. This PR does
not change production retrieval.

## Previously unresolved PR11 categories

| Root cause | Cases | Oracle Hit@1 | Hit@3 | Hit@5 | MRR@5 |
|---|---:|---:|---:|---:|---:|
| CANDIDATE_GENERATION_TRUNCATION | 6 | 50.0% | 50.0% | 66.7% | 0.542 |
| CHILD_RESELECTION_LOSS | 4 | 75.0% | 100.0% | 100.0% | 0.875 |
| CHUNKING_BOUNDARY_LOSS | 1 | 0.0% | 0.0% | 0.0% | 0.000 |
| DOCUMENT_RETRIEVED_WRONG_CHUNK | 8 | 12.5% | 50.0% | 87.5% | 0.358 |
| FUSION_OR_RERANK_LOSS | 5 | 80.0% | 100.0% | 100.0% | 0.900 |
| GOLD_OR_EVAL_DEFINITION_ISSUE | 3 | 0.0% | 0.0% | 0.0% | 0.000 |

## Remaining oracle misses

Eight cases remain outside the oracle top five: three committed gold/evaluation gaps,
one known chunk-boundary loss, two candidate-generation cases that also remain weak
within the document, one wrong-chunk case, and one otherwise successful global case.
These remain visible rather than being removed from the denominator.

## Verification

- Unit suite: **551 passed, 16 skipped**
- PostgreSQL integration: **16 passed**
- Schedule SQL: **150/150**
- Schedule NLU: **150/150**
- Schedule renderer: **140/140**
- Course Details retrieval: **120/120 Hit@1 and Hit@5**
- Academic Calendar: **20/20 route and Hit@5**
- Ruff check, Ruff format check, and `git diff --check`: **PASS**
- PR10 frozen snapshot SHA-256: `4adf938f211f5934884cf798d15ee66bbab3dbf1c2051ba13579cdfb9f2c36f5`
- PR10 frozen taxonomy SHA-256: `42f67f9fd3a4fd738b26a89ba36200bd1982928842d42078729198026d58fe64`
- Total recorded PR12 worktree API usage: **$0.00030132 / $3.00**
