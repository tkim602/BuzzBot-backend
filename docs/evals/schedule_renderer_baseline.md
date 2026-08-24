# Schedule Renderer Baseline

- Evaluated code SHA: `f50045e9652c136814ef75321780077a7453d00e`
- Dataset: `schedule-mvp-v1`
- Manifest SHA-256: `a459448c88ac7969ffc45f76db0a10dd02f1ddc34a7e400b2e6de35246876e89`
- Base data version: `bf5473f7-5d3f-4b3d-a2ba-eb8b84a54a60`
- Cases: 140
- Factual correctness: **140/140 (100%)**
- Structured SQL regression: **150/150 (100%)**
- Unsupported online/open-seat wording: **0**
- Cost: **$0**
- LangSmith experiment: not used for this deterministic contract run
- Latency: not benchmarked; this run isolates correctness on a local database
- Failure buckets: none

Known limitation: the frozen scope covers 20 Fall 2026 CS courses and seven
supported schedule query types. It does not claim live seat or waitlist status.
