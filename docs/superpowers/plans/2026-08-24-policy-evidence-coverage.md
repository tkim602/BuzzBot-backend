# PR11 Policy evidence coverage implementation plan

**Goal:** Raise decisive Policy evidence Hit@5 from the frozen PR10 baseline of 70% using only evidence-supported retrieval/data fixes, while preserving PR10's answer fixture and all existing gates.

**Baseline:** Branch from accepted PR10 head `d3e03c3`; `532 passed, 16 skipped`. Frozen snapshot SHA-256: `4adf938f211f5934884cf798d15ee66bbab3dbf1c2051ba13579cdfb9f2c36f5`.

## 1. Diagnose the fixed 30 misses

- Add one deterministic diagnostic command under `eval/quality/` that reuses the committed dev-100 manifest, gold evidence loader, retrieval functions, and DB models.
- For each frozen `evidence_hit_at_5=false` case, record corpus document presence, exact evidence in source/chunks, deeper vector/lexical candidate ranks, production top five, earliest failed stage, and one primary taxonomy label.
- Test classification and report validation with small in-memory inputs before running against PostgreSQL.
- Produce `eval/quality/policy_evidence_miss_diagnosis_pr11.json` and `docs/evals/policy_evidence_miss_diagnosis_pr11.md`.

## 2. Make the smallest justified production fix

- Select only the largest actionable root cause proven by the diagnosis.
- Write a focused failing regression test first.
- Reuse the existing retrieval/source/chunking path; add no dependency, generic abstraction, benchmark ID, hard-coded answer, or gold URL.
- Keep final top-k, validators, thresholds, official-source allowlists, and frozen PR10 fixture unchanged.

## 3. Measure and verify

- Run the same Policy dev-100 manifest and gold evidence from source; report document Hit@5 and decisive evidence Hit@1/3/5 plus MRR@5.
- Accept the iteration only if the change is principled and regressions pass; do not fabricate the `>=85%` target.
- If retrieval stabilizes, run one bounded Policy answer evaluation. Never overwrite the PR10 fixture.
- Run targeted tests, full unit suite, PostgreSQL integration, canonical structured/Calendar/Course Details gates, Ruff, formatting check, and `git diff --check`.
- Recheck frozen fixture hashes, write the PR11 final report/artifacts, and prepare a PR-ready commit as `tkim602 <tkim602@gatech.edu>`.
