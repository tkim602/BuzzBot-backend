# BuzzBot PR11 Policy evidence-coverage report

Date: 2026-08-24

Branch: `data/policy-evidence-coverage`

Baseline: accepted PR10 head `d3e03c3`

## Decision

PR11 found and fixed one real table-chunking defect without weakening retrieval or answer safety, but it did **not** meet the Policy decisive-evidence target. The safe result is 70% Evidence Hit@5, below the 85% gate. This PR is suitable as a diagnostic/data-correctness change, not as closure of Policy retrieval quality.

## Root-cause diagnosis

All 30 frozen PR10 evidence misses were assigned exactly one primary cause. No gold document was absent, no intended source was outside the corpus, and no routed source excluded its gold document.

| Root cause | Before | After |
|---|---:|---:|
| Candidate-generation truncation | 5 | 6 |
| Child reselection loss | 4 | 4 |
| Chunking-boundary loss | 3 | 1 |
| Correct document, wrong chunk | 8 | 8 |
| Fusion/rerank loss | 5 | 5 |
| Gold/eval definition issue | 3 | 3 |
| Resolved since frozen PR10 | 2 | 3 |

The candidate-truncation count rises by one after reindex because `gold-071-v10` now has a valid single evidence chunk; retrieval, rather than chunk availability, becomes its earliest failing stage.

## Accepted production change

The generic HTML table serializer emits relationships such as `Document Deadline — Fall Semester: March 16`. The chunker incorrectly classified those rows as headings, splitting related table values into separate sections. PR11 now treats `Field — Column: Value` rows as structured content and increments `CHUNKING_VERSION` from 2 to 3 so unchanged trusted documents use the existing safe reindex path.

Bounded reindex verification:

- `gt-transfer-admission` deadline URL: 3 → 2 chunks; decisive span now survives; 915 embedding tokens.
- `gt-dining` meal-plan URL: 7 → 6 chunks; decisive span now survives; 2,946 embedding tokens.
- `gold-030-v10` became a new Evidence Hit@5.
- No source-run evidence regression was observed.

## Policy retrieval

| Metric | Frozen PR10 | PR11 source-run before | PR11 final | Gate | Status |
|---|---:|---:|---:|---:|---|
| Document Hit@5 | 80% | 79% | 79% | report | — |
| Evidence Hit@1 | — | 37% | 39% | report | — |
| Evidence Hit@3 | — | 59% | 60% | report | — |
| Evidence Hit@5 | 70% | 69% | 70% | >=85% | FAIL |
| Document MRR@5 | 0.640 | 0.627 | 0.633 | no material regression | PASS |

The frozen and source-run baselines differ by six cases because the live corpus/retrieval state changed after the PR10 fixture was frozen. Both baselines remain visible; the committed PR10 fixture was not modified.

## Rejected bounded experiments

| Experiment | Evidence Hit@5 | Document Hit@5 | Decision |
|---|---:|---:|---|
| Vector candidates 5 → existing fusion budget 15 | 69% | 78% | Rejected: one win and one loss; no net evidence gain |
| Second cross-encoder over parent + child candidates | 64% | 78% | Rejected: 6 wins, 11 evidence regressions, mean latency 2.05s → 3.00s |

Neither experiment remains in production code.

## Remaining frozen misses

- Candidate truncation: `gold-009-v10`, `gold-010-v10`, `gold-011-v10`, `gold-012-v2`, `gold-071-v10`, `gold-088-v4`.
- Child reselection: `gold-006-v7`, `gold-039-v10`, `gold-051-v7`, `gold-062-v9`.
- Correct document, wrong chunk: `gold-004-v10`, `gold-007-v10`, `gold-063-v7`, `gold-073-v10`, `gold-079-v7`, `gold-083-v7`, `gold-091-v3`, `gold-092-v10`.
- Fusion/rerank: `gold-013-v8`, `gold-014-v8`, `gold-050-v10`, `gold-054-v10`, `gold-060-v3`.
- Chunk boundary: `gold-024-v3`; its committed span crosses two genuine recommendation headings.
- Gold/corpus evidence unavailable: `gold-040-v10`, `gold-049-v10`, `gold-072-v10`.

## Answer-quality evaluation

No new paid Policy end-to-end run was performed. The retrieval gate did not improve beyond the frozen 70% baseline, so rerunning answer synthesis would not isolate a stabilized retrieval improvement. PR10 answer metrics remain the accepted answer-layer baseline: 71% correctness, 92% support, 78% aggregate citation entailment, and 0% unsupported-confident answers.

## Regression gates

| Gate | Result |
|---|---:|
| Policy decisive Evidence Hit@5 | 70 / 100 (target 85, FAIL) |
| Schedule SQL | 150 / 150 |
| Schedule NLU | 150 / 150 |
| Schedule renderer | 140 / 140 |
| Course Details Hit@1 / Hit@5 | 120 / 120 |
| Calendar route / Hit@5 | 20 / 20 |
| Unit suite | 547 passed, 16 skipped |
| PostgreSQL integration | 16 passed |
| Ruff / formatting / diff | PASS |
| PR11-recorded API usage | $0.0006 / $3.00 |

## Artifacts

- Baseline diagnosis: `eval/quality/policy_evidence_miss_diagnosis_pr11.json`
- Post-fix diagnosis: `eval/quality/policy_evidence_miss_diagnosis_pr11_after.json`
- Machine-readable metrics: `eval/quality/policy_retrieval_pr11.json`
- Frozen fixture SHA-256 remains `4adf938f...36f5` for `snapshot.json` and `42f67f9f...e64` for `taxonomy.json`.
