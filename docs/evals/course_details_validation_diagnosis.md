# Course Details Validation Diagnosis

- Baseline experiment: `buzzbot-course-details-baseline-c0531c46`
- Baseline git SHA: `c33866a5409962ac31de1eff6f2535f4d1ff2988`
- Cases inspected: 14 `ANSWER_VALIDATION_REJECT`
- Method: read the existing LangSmith root, `answer`, and `validate_answer` runs; no new target or LLM calls

## Result

| Primary cause | Cases | Count |
|---|---|---:|
| `WRONG_COURSE_CHUNK` | 003–009, 013–017, 020 | 13 |
| `CLAIM_VALIDATOR_FALSE_REJECT` | 012 | 1 |

The dominant problem is course-specific retrieval, not the validator. Thirteen
rejected runs did not contain the requested course marker in any returned
chunk. URL Hit@K had reported success because all chunks shared the CS catalog
URL.

Recomputed over all 20 preserved baseline traces:

```text
target-course chunk Hit@1 = 25%
target-course chunk Hit@5 = 25%
target-course chunk Hit@8 = 25%
target-course chunk MRR@8 = 0.25
```

## Per-case evidence

`G/C/P/N` means grounding valid / claims supported / polarity consistent /
answer non-empty. Legacy baseline traces did not retain chunk IDs; current graph
instrumentation now records `metadata.chunk_id` for future experiments.

| Case | Target rank | Retrieved evidence sample | Pre-validation answer | Citations before/after | G/C/P/N | Primary classification |
|---|---:|---|---|---:|---|---|
| 003 | — | CS 2316, 2340, 2698… | CS 6300 is Software Development Process, covers software-engineering principles and practices, and is 3 credits. | 3/3 | T/F/T/T | `WRONG_COURSE_CHUNK` |
| 004 | — | CS 2316, 2340, 2698… | CS 6310 is not listed in the retrieved sources. | 0/0 | F/F/T/T | `WRONG_COURSE_CHUNK` |
| 005 | — | CS 2316, 2340, 2698… | CS 6340 covers challenges faced by underserved populations and developing countries and is 3 credits. | 3/3 | T/F/T/T | `WRONG_COURSE_CHUNK` |
| 006 | — | CS 6200, 6210, 4210… | CS 6515 covers challenges faced by underserved populations and developing countries and is 3 credits. | 3/3 | T/F/T/T | `WRONG_COURSE_CHUNK` |
| 007 | — | CS 2316, 2340, 2698… | CS 6601 covers Artificial Intelligence and is 3 credits. | 2/2 | T/F/T/T | `WRONG_COURSE_CHUNK` |
| 008 | — | CS 2316, 2340, 2698… | CS 6603 covers unspecified computer-science topics; details and credits were not retrieved. | 0/0 | F/F/T/T | `WRONG_COURSE_CHUNK` |
| 009 | — | CS 2316, 2340, 2698… | CS 6604 is not listed in the retrieved sources. | 0/0 | F/F/T/T | `WRONG_COURSE_CHUNK` |
| 012 | 1 | Exact CS 7638 title, credits, description and topics | CS 7638 is Artificial Intelligence Techniques for Robotics, 3 credits, covering autonomous-vehicle AI, probabilistic inference, filters, planning/search, PID control, and SLAM. | 4/4 | T/F/T/T | `CLAIM_VALIDATOR_FALSE_REJECT` |
| 013 | — | CS 6266, 6267, 6268… | CS 7641 covers machine learning and is 3 credits. | 2/2 | T/F/T/T | `WRONG_COURSE_CHUNK` |
| 014 | — | CS 6266, 6267, 6268… | CS 7642 covers machine learning and artificial intelligence and is 3 credits. | 2/2 | T/F/T/T | `WRONG_COURSE_CHUNK` |
| 015 | — | CS 2316, 2340, 2698… | CS 7643 covers deep learning and is 3 credits. | 2/2 | T/F/T/T | `WRONG_COURSE_CHUNK` |
| 016 | — | CS 7616, 7626, 7630… | CS 7646 covers machine learning and its applications and is 3 credits. | 3/3 | T/F/T/T | `WRONG_COURSE_CHUNK` |
| 017 | — | CS 6200, 6210, 4210… | CS 7650 covers challenges faced by underserved populations and developing countries and is 3 credits. | 3/3 | T/F/T/T | `WRONG_COURSE_CHUNK` |
| 020 | — | CS 2316, 2340, 2698… | CS 7750 is not listed in the retrieved sources. | 0/0 | F/F/T/T | `WRONG_COURSE_CHUNK` |

## Case 012 false reject

The decisive evidence contains:

```text
CS 7638. Artificial Intelligence Techniques for Robotics. 3 Credit Hours.
AI techniques with applications to autonomous vehicles. Extensive programming
exercises. Topics include probabilistic inference, Kalman/particle filters,
planning/search algorithms, PID control, SLAM.
```

The generated answer and all four citation quotes were grounded in that span,
but claim validation returned `INSUFFICIENT`. This is a real validator false
reject, but it is only one of the fourteen failures and should not be tuned
before the larger 13-case retrieval bucket.

## Next bounded hypothesis

Course Details retrieval should require the exact requested course marker to
survive candidate selection/reranking. The next quality change should target
that course-specific retrieval boundary and leave the claim validator unchanged
until the retrieval experiment is measured.
