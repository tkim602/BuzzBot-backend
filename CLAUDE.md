# BuzzBot — Project Instructions for Claude

## Credential Security (MANDATORY)
- NEVER hardcode API keys, tokens, secrets, or passwords in source code.
- ALWAYS store credentials in a `.env` file and load them via environment variables.
- If a new API key or secret is needed, create or update the `.env` file and add the variable name to `.env.example` (without the actual value).
- Before committing, verify that no secrets appear in staged files.
- The `.gitignore` already excludes `.env` — do not remove that rule.

## Project Structure
- `prompts/` — LLM prompt templates (numbered by pipeline stage)
- `schemas/` — JSON schemas for structured LLM outputs
- `eval/` — Evaluation golden sets, metrics, and rubrics
- `.claude/skills/` — Claude Code slash command skills

## Conventions
- All LLM-facing prompts that return structured data must output valid JSON only.
- Use the schemas in `schemas/` to validate outputs.
- Prefer official Georgia Tech sources for any retrieval or citation.
