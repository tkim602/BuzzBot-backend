# English Schedule NLU Baseline

- Evaluated code SHA: `f50045e9652c136814ef75321780077a7453d00e`
- Dataset: `schedule-mvp-v1`
- Manifest SHA-256: `a459448c88ac7969ffc45f76db0a10dd02f1ddc34a7e400b2e6de35246876e89`
- Cases: 150
- Route/query type/course/term success: **150/150 (100%)**
- Clarification accuracy: **10/10 (100%)**
- Unsafe guess rate: **0/150 (0%)**
- Cost: **$0**
- LangSmith experiment: not used for this deterministic contract run
- Latency: not benchmarked; parsing is local and deterministic
- Failure buckets after fix: none

The frozen set includes compact and hyphenated course codes, casual instructor
wording, reversed term order, online questions, bounded follow-ups, missing
course/term cases, and unresolved relative-term wording. Relative terms remain a
safe clarification rather than being guessed.
