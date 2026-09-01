# Policy RAG dev-100 baseline

Date: 2026-08-24  
Manifest: `dev_100.json`  
Manifest SHA-256: `58c343d37902a4bbbf4f281509413b701d55cca6cbeab8001404111de6c59562`

## Retrieval

The candidate fetches a bounded deeper OR-lexical pool, reranks it with the
existing lexical scorer, applies canonical-URL diversity, then truncates to
the unchanged downstream fusion budget.

| Metric | Frozen baseline | Candidate |
|---|---:|---:|
| Hit@5 | 57% | 80% |
| MRR@5 | 0.4002 | 0.6398 |
| Wins / regressions | — | 27 / 4 |
| Empty retrieval | — | 2% |

Acceptance gate (`Hit@5 >= 62%`, non-decreasing MRR, wins > regressions):
**PASS**.

## Live `/chat` evaluation

One sequential 100-case production-contract run was executed after the
retrieval gate passed.

| Metric | Result |
|---|---:|
| Completed | 100 / 100 |
| Answer correctness | 68% |
| Evidence support | 71% |
| Supported and cited | 71% |
| Gold citation URL hit | 61% |
| Abstention | 19% |
| p50 / p95 latency | 5.88s / 9.71s |
| Evaluation cost | $0.06410178 |

The 70% Policy task-success target is **NOT MET** by two cases. Failure
diagnosis separates 20 retrieval misses from 21 answer/citation/validation
failures; the latter is now the largest next workstream. The system remains
fail-closed rather than weakening validation to inflate the score.
