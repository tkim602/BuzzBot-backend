# BuzzBot Course Details LangSmith Candidate

- Evaluated code SHA: `52f8c01678f02ac63183a44ed7a297a42d739e29`
- Dataset: `buzzbot-course-details-20-full-domain-v1`
- Source dataset SHA-256: `8241477b0f886353cd28a1a2c645fccfe9b3ebf8fe29e9f861b5e40d19efb2c3`
- Cases: 20
- Experiment: [buzzbot-course-details-baseline-6befa879](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/datasets/e9fcfc8b-121d-40db-bff7-777e958317b7/compare?selectedSessions=08c2dc64-20f3-4ec3-add0-a4b5d2f5cb21)
- Target-course chunk Hit@1: **20/20 (100%)**
- Target-course chunk Hit@5: **20/20 (100%)**
- Final answer accepted with gold citation: **18/20 (90%)**
- Semantic judge coverage: **19/20**
- Answer correctness among judged: **16/19 (84.2%)**
- Support among judged: **17/19 (89.5%)**
- Fail-closed task success: **16/20 (80%)**
- Abstention: **2/20 (10%)**
- Root-run cost: **$0.0125808**
- Root-run tokens: **73,132**
- Latency p50/p99: **4.90 / 5.87 seconds**

## Remaining failures

- `ANSWER_VALIDATION_REJECT`: 2 (`CS 6515`, `CS 7638`)
- `SYNTHESIS_WRONG`: 1 (`CS 6035`, supported but judged incomplete against gold)
- Missing semantic feedback: 1 (`CS 7641`)
- Two runs did not persist `primary_failure_stage`; one is the missing-feedback run

The experiment completed all 20 target runs. Local report generation then
failed because the installed LangSmith SDK exposes `RunTree.get_url()` instead
of the older `.url` property. That reporting compatibility bug is covered by a
regression test and fixed without rerunning the paid experiment.

## Decision

The dominant exact-course retrieval failure is closed: the preserved 20-case
baseline improved from 25% to 100% target-course Hit@1, and the expanded
120-case retrieval set also scored 100%. Course Details reaches the 80% MVP
task-success gate. Validator-specific tuning is deferred to a separate
hypothesis rather than mixed into this retrieval change.
