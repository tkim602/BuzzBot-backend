"""LLM answerer — generates cited answers in strict JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path

import structlog

from app.core.config import settings
from app.core.usage import check_limit_or_raise, record_usage
from app.rag.retrieval import RetrievedChunk

logger = structlog.get_logger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    if path.exists():
        return path.read_text()
    return ""


def _lost_in_the_middle_reorder(items: list) -> list:
    """Reorder items so the most relevant are at the start and end.

    Exploits the U-shaped attention pattern in LLMs: they attend most to the
    beginning and end of the context window. Places #1 first, #2 last, #3
    second, #4 second-to-last, etc.
    """
    if len(items) <= 2:
        return items
    reordered: list = [None] * len(items)
    left, right = 0, len(items) - 1
    for i, item in enumerate(items):
        if i % 2 == 0:
            reordered[left] = item
            left += 1
        else:
            reordered[right] = item
            right -= 1
    return reordered


def _build_context(chunks: list[RetrievedChunk], max_tokens: int = 3000) -> str:
    """Build context string from retrieved chunks, respecting token limit."""
    # First pass: select chunks within token budget
    selected: list[RetrievedChunk] = []
    approx_tokens = 0
    for c in chunks:
        chunk_tokens = len(c.chunk_text.split()) * 1.3  # rough token estimate
        if approx_tokens + chunk_tokens > max_tokens:
            break
        selected.append(c)
        approx_tokens += chunk_tokens

    # Apply lost-in-the-middle reordering
    selected = _lost_in_the_middle_reorder(selected)

    context_parts: list[str] = []
    for c in selected:
        source_label = f"[Source: {c.url}]" if c.url else "[Source: unknown]"
        fetched_label = f"[Fetched: {c.fetched_at}]" if c.fetched_at else ""
        context_parts.append(f"{source_label} {fetched_label}\n{c.chunk_text}")
    return "\n\n---\n\n".join(context_parts)


def _format_history(history: list[dict] | None) -> str:
    if not history:
        return "none"
    lines: list[str] = []
    for turn in history[-6:]:
        role = (turn.get("role") or "user").upper()
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "none"


async def generate_answer(
    query: str,
    chunks: list[RetrievedChunk],
    intent: str = "general",
    rmp_excerpt: str | None = None,
    user_context: dict | None = None,
    current_date: str | None = None,
    current_term: str | None = None,
    history: list[dict] | None = None,
) -> dict:
    """Generate a cited answer from retrieved contexts.

    Returns dict matching ChatResponse schema.
    """
    system_prompt = _load_prompt("chat_system.txt")
    user_template = _load_prompt("chat_user_template.txt")

    context_str = _build_context(chunks, max_tokens=settings.rag_max_context_tokens)

    # Add RMP excerpt if provided
    if rmp_excerpt:
        context_str += (
            "\n\n---\n\n[Source: user-provided:rmp] [Type: User-provided RateMyProfessors excerpt — unofficial]\n"
            + rmp_excerpt
        )

    user_msg = user_template.replace("{{QUERY}}", query).replace("{{CONTEXT}}", context_str)
    if user_context:
        user_msg = user_msg.replace("{{USER_CONTEXT}}", json.dumps(user_context))
    else:
        user_msg = user_msg.replace("{{USER_CONTEXT}}", "none")
    user_msg = user_msg.replace("{{CURRENT_DATE}}", current_date or "unknown")
    user_msg = user_msg.replace("{{CURRENT_TERM}}", current_term or "unknown")
    user_msg = user_msg.replace("{{CHAT_HISTORY}}", _format_history(history))

    # Call LLM
    raw_response = await _call_llm(system_prompt, user_msg)

    # Parse JSON from response
    try:
        parsed = _extract_json(raw_response)
    except Exception:
        logger.warning("failed to parse LLM JSON, wrapping raw response")
        parsed = {
            "answer": raw_response,
            "citations": [],
            "confidence": 0.5,
            "notes": ["Response was not in expected JSON format."],
        }

    return parsed


async def _call_llm(system: str, user: str) -> str:
    """Call the configured LLM provider."""
    # Check usage limit before API call
    check_limit_or_raise()
    
    provider = settings.llm_provider

    if provider == "openai":
        import openai

        client = openai.AsyncOpenAI()
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=1500,
        )
        
        # Record usage
        if resp.usage:
            record_usage(settings.openai_model, resp.usage.prompt_tokens, "input")
            record_usage(settings.openai_model, resp.usage.completion_tokens, "output")
        
        return resp.choices[0].message.content or ""

    elif provider == "anthropic":
        import anthropic

        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        
        # Record usage
        if hasattr(resp, 'usage'):
            record_usage(settings.anthropic_model, resp.usage.input_tokens, "input")
            record_usage(settings.anthropic_model, resp.usage.output_tokens, "output")
        
        return resp.content[0].text

    elif provider == "ollama":
        import httpx

        async with httpx.AsyncClient(timeout=120) as http:
            resp = await http.post(
                f"{settings.ollama_base_url}/api/chat",
                json={
                    "model": settings.ollama_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                },
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")

    raise ValueError(f"Unknown LLM provider: {provider}")


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response text."""
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON block
    import re

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))

    # Try to find bare JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))

    raise ValueError("No JSON found in response")
