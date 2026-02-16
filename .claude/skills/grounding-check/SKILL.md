---
name: grounding-check
description: Check a draft answer for unsupported claims against retrieved contexts. Returns JSON verdict.
---

Read the system prompt from `prompts/00_system_core.md` and the grounding check instructions from `prompts/40_grounding_check.md`.

Given a draft answer JSON and retrieved contexts, detect any unsupported or overstated claims. Return ONLY valid JSON with: verdict, unsupported_claims, citation_issues, suggested_edits.

If no draft answer or contexts are provided, ask the user for them.
