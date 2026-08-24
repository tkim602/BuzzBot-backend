# BuzzBot MVP quality report

Date: 2026-08-24  
Branch: `data/langsmith-eval`

## Reproducible gates

| Domain | Gate | Result | Status |
|---|---|---:|---|
| Structured schedule SQL | Exact result | 150 / 150 | PASS |
| Schedule renderer | Factual correctness | 140 / 140 | PASS |
| English schedule NLU | Route/slot/clarification | 150 / 150 | PASS |
| Schedule safety | Unsupported confident guesses | 0 / 10 | PASS |
| Course Details retrieval | Exact course Hit@1/Hit@5 | 120 / 120 | PASS |
| Course Details chat | Task success | 16 / 20 (80%) | PASS |
| Policy retrieval | Hit@5 / MRR@5 | 80% / 0.6398 | PASS |
| Policy chat | Correct / supported | 68% / 71% | BELOW 70% TARGET |
| Academic Calendar retrieval | Route / event Hit@5 | 100% / 100% | PASS |
| Academic Calendar chat | Task success | Not measured | NOT YET MEASURABLE |

## Engineering verification

| Check | Result |
|---|---:|
| Unit tests | 512 passed, 16 skipped |
| PostgreSQL integration | 16 passed |
| Ruff | 148 files clean/formatted |
| Usage after evaluation | $0.0924 / $3.00 |

## Decision

BuzzBot is a **usable structured-schedule and course-information MVP** with a
materially improved general Policy retrieval layer. It is not yet a fully
qualified all-domain Georgia Tech assistant: Policy answer quality is two
points below its target and Calendar answer quality has not been measured.

The next isolated workstream is answer synthesis/citation/validation. The
current dev-100 diagnosis contains 20 retrieval misses and 21 answer-layer
failures, so broad retrieval tuning should not continue. Validation remains
fail-closed; no threshold was weakened to improve the score.

