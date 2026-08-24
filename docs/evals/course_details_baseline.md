# BuzzBot Course Details Baseline

- Evaluated code SHA: `9a66b98d540b55dd9a3e91664a47d4f01196c683`
- Dataset: `course-details-120-v1`
- Manifest SHA-256: `fbeefa15b0d1cad67fc3bafc2318d7d7edf16cb10a7781f288dcf0ca85d0910a`
- Cases: 120
- Target-course chunk Hit@1: **120/120 (100%)**
- Target-course chunk Hit@5: **120/120 (100%)**
- Target-course MRR@5: **1.0000**
- Misses: **0**
- OpenAI cost: **$0.000042** (batched query embeddings only)
- Local latency: **220.8 seconds** total with CPU cross-encoder
- LangSmith experiment: pending bounded answer-level comparison
- Failure buckets: none at the retrieval boundary

The previous preserved 20-case traces measured 25% target-course Hit@5. The
root cause was same-URL child-vector reselection discarding an already found
exact course chunk. The fix preserves that generic exact-course lexical anchor;
it does not change chunking, the claim validator, or Catalog scope.

Known limitation: the frozen answer gold covers title, credits, and description
for 20 CS courses. It does not yet establish prerequisite/restriction quality
across all Catalog subjects.
