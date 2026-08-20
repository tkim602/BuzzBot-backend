"""LLM answerer — generates cited answers in strict JSON."""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

from app.core.config import settings
from app.core.usage import check_limit_or_raise, record_usage
from app.rag.retrieval import RetrievedChunk, _lexical_match_score

logger = structlog.get_logger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
FACTUAL_INTENTS = {
    "registrar_calendar",
    "admissions_deadline",
    "catalog_course",
    "course_schedule_sections",
    "policy",
}
_BINARY_QUESTION_RE = re.compile(
    r"^\s*(?:am|are|can|could|did|do|does|has|have|is|must|should|was|were|will|would)\b",
    re.I,
)
_LEADING_ANSWER_RE = re.compile(r"^\s*(?:yes|no)\b\s*[,.;:—–-]?\s*", re.I)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_QUOTE_WORD_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)?", re.I)


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


def _ground_citation_quotes(
    citations: object, chunks: list[RetrievedChunk], answer: str
) -> list[dict]:
    grounded: list[dict] = []
    for citation in citations if isinstance(citations, list) else []:
        if not isinstance(citation, dict) or not chunks:
            continue
        chunk = max(chunks, key=lambda item: _lexical_match_score(answer, item))
        if _lexical_match_score(answer, chunk) <= 0:
            continue
        texts = [chunk.chunk_text]
        grounded_citation = {
            **citation,
            "url": chunk.url,
            "title": chunk.title,
            "fetched_at": chunk.fetched_at,
        }
        quote = str(citation.get("quote") or "").strip()
        for text in texts:
            start = text.lower().find(quote.lower()) if quote else -1
            if start >= 0:
                grounded.append({**grounded_citation, "quote": text[start : start + len(quote)]})
                break
        else:
            quote_words = set(_QUOTE_WORD_RE.findall(quote.lower()))
            candidates = []
            for text in texts:
                sentences = [
                    part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()
                ]
                candidates.extend(sentences)
                candidates.extend(
                    f"{left} {right}" for left, right in zip(sentences, sentences[1:], strict=False)
                )
            best = max(
                candidates,
                key=lambda candidate: len(
                    quote_words & set(_QUOTE_WORD_RE.findall(candidate.lower()))
                ),
                default="",
            )
            if best and quote_words & set(_QUOTE_WORD_RE.findall(best.lower())):
                grounded.append({**grounded_citation, "quote": best})
    return grounded


async def _binary_proposition_verdict(query: str, evidence: str) -> str:
    try:
        proposition = await _call_llm(
            (
                "Extract the single atomic factual proposition that a Yes answer would assert. "
                "Return only that proposition as a declarative sentence, with no verdict or "
                "explanation. Preserve negation, numbers, dates, modality, conditions, and "
                "exceptions from the question."
            ),
            f"QUESTION:\n{query.strip()}",
            temperature=0.0,
            max_tokens=64,
        )
    except Exception:
        logger.warning("binary proposition extraction failed")
        return "UNKNOWN"
    proposition = proposition.strip()
    if not proposition:
        return "UNKNOWN"

    from app.rag.grounding import semantic_claim_verdict

    logger.debug("binary proposition verification", proposition=proposition, evidence=evidence)
    verdict = await semantic_claim_verdict(proposition, evidence)
    return {
        "SUPPORTED": "TRUE",
        "CONTRADICTED": "FALSE",
        "INSUFFICIENT": "UNKNOWN",
    }[verdict]


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

    binary_verdict: str | None = None
    polarity: str | None = None
    if intent in FACTUAL_INTENTS and _BINARY_QUESTION_RE.search(query):
        binary_verdict = await _binary_proposition_verdict(query, context_str)
        if binary_verdict == "UNKNOWN":
            return {
                "answer": "I don't have enough evidence to answer that yes/no question reliably.",
                "citations": [],
                "confidence": 0.2,
                "notes": ["The proposition could not be established from retrieved evidence."],
            }
        polarity = "Yes" if binary_verdict == "TRUE" else "No"
        proposition_truth = "true" if binary_verdict == "TRUE" else "false"
        system_prompt += (
            f"\n\nThe authoritative polarity is {polarity}; the question's proposition is "
            f"{proposition_truth}. Generate the evidence-grounded explanation body only and "
            f"explain why the proposition is {proposition_truth}. You must not restate the "
            "proposition with the opposite truth value. Do not choose or write a leading Yes or No."
        )

    user_msg = user_template.replace("{{QUERY}}", query).replace("{{CONTEXT}}", context_str)
    if user_context:
        user_msg = user_msg.replace("{{USER_CONTEXT}}", json.dumps(user_context))
    else:
        user_msg = user_msg.replace("{{USER_CONTEXT}}", "none")
    user_msg = user_msg.replace("{{CURRENT_DATE}}", current_date or "unknown")
    user_msg = user_msg.replace("{{CURRENT_TERM}}", current_term or "unknown")
    user_msg = user_msg.replace("{{CHAT_HISTORY}}", _format_history(history))

    temperature = 0.0 if intent in FACTUAL_INTENTS else 0.2

    # Call LLM
    raw_response = await _call_llm(system_prompt, user_msg, temperature=temperature)

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

    parsed["citations"] = _ground_citation_quotes(
        parsed.get("citations"), chunks, str(parsed.get("answer", ""))
    )
    if binary_verdict and polarity:
        body = _LEADING_ANSWER_RE.sub("", str(parsed.get("answer", "")), count=1)
        parsed["answer"] = f"{polarity}, {body}" if body else f"{polarity}."
        parsed["_binary_verdict"] = binary_verdict

    return parsed


async def _call_llm(
    system: str, user: str, temperature: float = 0.2, max_tokens: int = 1500
) -> str:
    """Call the configured LLM provider."""
    # Check usage limit before API call
    check_limit_or_raise()

    provider = settings.llm_provider

    if provider == "openai":
        import openai

        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
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
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        # Record usage
        if hasattr(resp, "usage"):
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
                    "options": {"num_predict": max_tokens},
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
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))

    # Try to find bare JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))

    raise ValueError("No JSON found in response")
