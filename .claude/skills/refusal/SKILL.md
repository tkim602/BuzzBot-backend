---
name: refusal
description: Generate a transparent uncertainty/refusal response when contexts are insufficient.
---

Read the system prompt from `prompts/00_system_core.md` and the refusal instructions from `prompts/50_refusal_and_uncertainty.md`.

When retrieved contexts are insufficient to answer a user's question, generate a short, transparent response that:
- States what could not be found
- Suggests next steps (ask for missing info, or points to official pages)
- Does not guess deadlines, policies, or numbers

If no query is provided as an argument, ask the user for one.
