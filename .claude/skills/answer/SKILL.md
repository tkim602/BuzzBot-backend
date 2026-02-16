---
name: answer
description: Generate a cited answer from retrieved contexts. Returns JSON matching the chat_response schema.
---

Read the system prompt from `prompts/00_system_core.md` and the answer generator instructions from `prompts/30_answer_with_citations_json.md`.
Also load the response schema from `schemas/chat_response.schema.json`.

Given a user question and retrieved contexts, generate an answer following the prompt instructions. Return ONLY valid JSON that conforms to the chat_response schema.

Every factual claim must have a citation. If contexts are insufficient, set confidence to "low" and needs_followup to true.

If no query or contexts are provided, ask the user for them.
