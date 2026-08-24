# BuzzBot PR10 answer-quality report

Date: 2026-08-24

Branch: `data/policy-answer-quality`

## Frozen-evidence Policy result

Retrieval is fixed to the committed PR9 dev-100 top-five snapshot. These
numbers therefore measure answer synthesis, citation selection, and validation
without retrieval drift.

| Metric | Result | Gate | Status |
|---|---:|---:|---|
| Answer correctness | 71% | >= 70% | PASS |
| Answer support | 92% | >= 75% | PASS |
| Citation entails claim, all cases | 78% | >= 90% | FAIL |
| Citation entails claim, evidence-hit cases | 94.3% | >= 90% | PASS |
| Unsupported confident answers | 0% | 0% | PASS |

The aggregate citation gate remains below target because 30 cases do not
contain the exact gold evidence in the frozen top five. The 70 cases with
decisive evidence pass the citation gate. No validator or relevance threshold
was weakened to obtain this result.

Compared with the first same-snapshot PR10 run, correctness improved from 64%
to 71%, support from 84% to 92%, and unsupported confident answers from 2% to
0%. The remaining result is 63 PASS, 30 incomplete, four unnecessary
abstentions, two citation mismatches, and one validator false rejection.

LangSmith: [Policy dev-100 experiment](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/dca1a597-daea-4ddd-92fa-c32154bd1937)

## Calendar answer result

The existing 20 official Calendar events were supplied as frozen evidence;
Calendar retrieval and ingestion were not changed.

| Metric | Result |
|---|---:|
| Answer correctness | 20 / 20 |
| Answer support | 20 / 20 |
| Citation entails claim | 20 / 20 |
| Citation source correct | 20 / 20 |
| Unsupported confident answers | 0 / 20 |

LangSmith: [Calendar answer experiment](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/829e8390-9b4c-4008-99fe-5adc4fe7c3d0)

## Deterministic regression gates

| Gate | Result |
|---|---:|
| Schedule SQL | 150 / 150 |
| Schedule NLU | 150 / 150 |
| Schedule renderer | 140 / 140 |
| Course Details retrieval Hit@1 / Hit@5 | 120 / 120 |
| Calendar route / Hit@5 | 20 / 20 |
| Unit suite | 532 passed, 16 skipped |
| PostgreSQL integration | 16 passed |
| Ruff / formatting / diff | PASS |

## Decision

PR10 closes the isolated answer-layer iteration: Policy clears correctness,
support, and safety; Calendar answer quality is now measurable and passes its
frozen-evidence suite. Full-domain Policy citation quality is not yet a 90%
aggregate result. Its next work item belongs to evidence availability and
retrieval coverage, not weaker answer validation.
