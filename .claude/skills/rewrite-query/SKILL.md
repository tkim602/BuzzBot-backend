---
name: rewrite-query
description: Rewrite a user question into high-recall search queries for retrieval. Returns JSON.
---

Read the system prompt from `prompts/00_system_core.md` and the rewriter instructions from `prompts/20_query_rewrite_retrieval.md`.

Given the user's query, rewrite it into keyword and semantic search queries following the prompt instructions. Return ONLY valid JSON with the fields: canonical_query, keyword_queries, semantic_queries, must_include, date_sensitivity.

If no query is provided as an argument, ask the user for one.
