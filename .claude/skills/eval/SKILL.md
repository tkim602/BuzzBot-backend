---
name: eval
description: Evaluate a BuzzBot answer using the rubric and metrics framework.
---

Read the evaluation rubric from `eval/eval_rubric.md`, the metrics definitions from `eval/metrics.md`, and the golden test set from `eval/golden_set.sample.jsonl`.

Given a BuzzBot answer (JSON), score it on the rubric dimensions:
1. Correctness (0-2)
2. Grounding / Faithfulness (0-2)
3. Citations Quality (0-2)
4. Freshness Handling (0-2) — only for freshness-sensitive queries

Provide an overall score (0-8) and specific feedback for each dimension.

If no answer is provided, ask the user for one or offer to run against the golden set samples.
