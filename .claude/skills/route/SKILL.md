---
name: route
description: Classify a user query by intent and decide if live fetch is needed. Returns JSON matching the router schema.
---

Read the system prompt from `prompts/00_system_core.md` and the router instructions from `prompts/10_router_intent_freshness.md`.
Also load the router schema from `schemas/router_decision.schema.json`.

Given the user's query, classify it according to the router prompt instructions and return ONLY valid JSON that conforms to the router_decision schema.

If no query is provided as an argument, ask the user for one.
