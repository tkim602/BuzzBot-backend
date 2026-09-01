# BuzzBot PR13 evaluation-only hierarchical Policy retrieval

- Cases: 100
- Production Evidence Hit@5: 70.0%
- Oracle-document Evidence Hit@5: 92.0%
- Production switch: not performed
- Paid semantic answer evaluation: not performed

## Bounded document-count comparison

| Documents | Doc recall | Evidence Hit@1 | Hit@3 | Hit@5 | MRR@5 | Wins | Regressions | Mean ms | p95 ms |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 46.0% | 32.0% | 39.0% | 44.0% | 0.363 | 4 | 30 | 2940.8 | 4523.1 |
| 2 | 64.0% | 31.0% | 51.0% | 56.0% | 0.410 | 5 | 19 | 3620.2 | 5707.9 |
| 3 | 76.0% | 31.0% | 48.0% | 61.0% | 0.417 | 6 | 15 | 4230.5 | 7153.6 |
| 5 | 83.0% | 31.0% | 48.0% | 62.0% | 0.419 | 6 | 14 | 5055.8 | 8392.9 |

## Decision

- No hierarchical candidate met the 85% Evidence Hit@5 gate.

The tested hierarchy is rejected as a production candidate. At `N=5`, Stage 1
document recall is only 83%, which is already below the 85% minimum Evidence
gate. Stage 2 and cross-document merge reduce the result further to 62%, with
6 wins and 14 regressions versus production. Mean latency also rises from the
production baseline of 2276 ms to 5056 ms, while p95 rises from 3902 ms to
8393 ms.

PR12's 92% oracle result therefore remains an upper-bound diagnosis, not proof
that the current document selector and merge strategy should replace production.
No retrieval threshold, top-k, production path, or PR12 oracle miss was changed.

## Verification

- Unit suite: **556 passed, 16 skipped**
- PostgreSQL integration: **16 passed**
- Schedule SQL: **150/150**
- Schedule NLU: **150/150**
- Schedule renderer: **140/140**
- Course Details retrieval: **120/120 Hit@1 and Hit@5**
- Academic Calendar: **20/20 route and Hit@5**
- Ruff check, Ruff format check, and `git diff --check`: **PASS**
- PR10 frozen snapshot SHA-256: `4adf938f211f5934884cf798d15ee66bbab3dbf1c2051ba13579cdfb9f2c36f5`
- PR10 frozen taxonomy SHA-256: `42f67f9fd3a4fd738b26a89ba36200bd1982928842d42078729198026d58fe64`
- Recorded branch API usage: **$0.00030132 / $3.00**
