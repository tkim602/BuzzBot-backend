"""Query rewriting for retrieval precision and date-term grounding."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog

from app.core.config import settings
from app.core.usage import check_limit_or_raise, record_usage
from app.rag.router import extract_course_code

logger = structlog.get_logger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

TERM_RE_1 = re.compile(r"\b(spring|summer|fall)\s*(20\d{2})\b", re.IGNORECASE)
TERM_RE_2 = re.compile(r"\b(20\d{2})\s*(spring|summer|fall)\b", re.IGNORECASE)
PRONOUN_RE = re.compile(
    r"\b(it|that class|this class|that course|this course|그거|그 과목|이 과목)\b", re.IGNORECASE
)
FOLLOWUP_SIGNALS = re.compile(
    r"\b(another|other|also|more|then|which semester|offered|available|alternatives?|instead|else|too)\b",
    re.IGNORECASE,
)
DATE_SENSITIVE_TERMS = (
    "when",
    "date",
    "deadline",
    "registration",
    "add/drop",
    "withdrawal",
    "commencement",
    "today",
    "tomorrow",
    "this semester",
    "next semester",
    "upcoming",
    "언제",
    "마감",
    "학사일정",
)
ADMISSIONS_QUERY_TERMS = (
    "application deadline",
    "admission deadline",
    "deadline to apply",
    "apply",
    "admission",
    "omscs",
    "mscs",
)
NEXT_TERM_TERMS = ("next semester", "upcoming", "next term", "다음 학기")
EXPLICIT_FACTUAL_TERMS = (
    "application deadline",
    "admission deadline",
    "deadline",
    "when is",
    "last day to",
    "registration",
    "register",
    "add/drop",
    "withdraw",
    "omscs",
    "mscs",
    "fall 20",
    "spring 20",
    "summer 20",
)


@dataclass
class RewriteResult:
    original_query: str
    rewritten_query: str
    current_date: str
    current_term: str
    date_sensitive: bool
    detected_course_code: str | None = None
    detected_term_name: str | None = None


@dataclass
class _TemporalContext:
    current_date: str
    current_term: str


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.exists():
        return ""
    return path.read_text()


def _normalize_space(text: str) -> str:
    return " ".join(text.split())


def _extract_course_code(query: str) -> str | None:
    return extract_course_code(query)


def _extract_term_name(query: str) -> str | None:
    m = TERM_RE_1.search(query)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    m = TERM_RE_2.search(query)
    if m:
        return f"{m.group(2).capitalize()} {m.group(1)}"
    return None


def _is_date_sensitive(query: str) -> bool:
    q = query.lower()
    return any(term in q for term in DATE_SENSITIVE_TERMS)


def _is_admissions_deadline_query(query: str) -> bool:
    q = query.lower()
    if "deadline" not in q:
        return False
    return any(term in q for term in ADMISSIONS_QUERY_TERMS)


def _term_for_month(month: int, year: int) -> str:
    if month <= 5:
        return f"Spring {year}"
    if month <= 7:
        return f"Summer {year}"
    return f"Fall {year}"


def _next_term(term: str) -> str:
    semester, year = term.split()
    y = int(year)
    if semester == "Spring":
        return f"Summer {y}"
    if semester == "Summer":
        return f"Fall {y}"
    return f"Spring {y + 1}"


def _get_temporal_context(now: datetime | None = None) -> _TemporalContext:
    now_utc = now or datetime.now(UTC)
    tz_name = settings.rag_user_timezone or "America/New_York"
    local = now_utc.astimezone(ZoneInfo(tz_name))
    current_date = local.date().isoformat()
    current_term = _term_for_month(local.month, local.year)
    return _TemporalContext(current_date=current_date, current_term=current_term)


def _extract_topic_keywords(text: str) -> list[str]:
    """Extract salient topic words from a message for context carry-forward."""
    stopwords = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "shall",
        "about",
        "for",
        "with",
        "from",
        "that",
        "this",
        "what",
        "which",
        "how",
        "when",
        "where",
        "who",
        "why",
        "there",
        "here",
        "any",
        "some",
        "all",
        "each",
        "every",
        "it",
        "its",
        "i",
        "me",
        "my",
        "you",
        "your",
        "we",
        "our",
        "they",
        "them",
        "their",
        "of",
        "in",
        "on",
        "at",
        "to",
        "and",
        "or",
        "but",
        "not",
        "no",
        "so",
        "if",
        "then",
        "than",
        "too",
        "very",
        "just",
        "tell",
        "know",
        "show",
        "give",
        "get",
        "find",
        "gt",
        "georgia",
        "tech",
    }
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return [w for w in words if w not in stopwords]


def _should_force_rule_rewrite(query: str, fallback: RewriteResult) -> bool:
    q = query.lower().strip()
    if any(term in q for term in EXPLICIT_FACTUAL_TERMS):
        return True
    # Date-sensitive questions are usually better served with low-latency deterministic rewrite.
    return fallback.date_sensitive and len(query.split()) >= 5


def _resolve_from_history(query: str, history: list[dict] | None) -> str:
    if not history:
        return query
    if _extract_course_code(query):
        return query

    has_pronoun = bool(PRONOUN_RE.search(query))
    has_followup_signal = bool(FOLLOWUP_SIGNALS.search(query))
    is_short = len(query.split()) <= 6

    needs_context = has_pronoun or has_followup_signal or is_short

    if not needs_context:
        return query

    # Try to find a course code in recent history
    for turn in reversed(history):
        text = (turn.get("content") or "").strip()
        code = _extract_course_code(text)
        if code:
            return f"{query} {code}"

    # No course code found — carry forward topic keywords from recent history
    for turn in reversed(history[-4:]):
        text = (turn.get("content") or "").strip()
        topics = _extract_topic_keywords(text)
        if topics:
            return f"{query} {' '.join(topics[:3])}"

    return query


def _build_rule_based_query(
    query: str,
    history: list[dict] | None,
    user_term: str | None,
    temporal: _TemporalContext,
) -> RewriteResult:
    standalone = _resolve_from_history(_normalize_space(query), history)
    date_sensitive = _is_date_sensitive(standalone)
    admissions_deadline = _is_admissions_deadline_query(standalone)
    detected_course_code = _extract_course_code(standalone)
    detected_term = _extract_term_name(standalone)

    target_term = detected_term or user_term
    if not target_term and date_sensitive and not admissions_deadline:
        wants_next = any(x in standalone.lower() for x in NEXT_TERM_TERMS)
        target_term = _next_term(temporal.current_term) if wants_next else temporal.current_term

    extras: list[str] = []
    lowered = standalone.lower()

    if target_term and target_term.lower() not in lowered:
        extras.append(target_term)
    if detected_course_code and detected_course_code.lower() not in lowered:
        extras.append(detected_course_code)

    rewritten = standalone if not extras else f"{standalone} {' '.join(extras)}"
    rewritten = _normalize_space(rewritten)

    return RewriteResult(
        original_query=query,
        rewritten_query=rewritten,
        current_date=temporal.current_date,
        current_term=temporal.current_term,
        date_sensitive=date_sensitive,
        detected_course_code=detected_course_code,
        detected_term_name=detected_term or target_term,
    )


async def _rewrite_with_llm(
    query: str,
    temporal: _TemporalContext,
    fallback: RewriteResult,
    history: list[dict] | None,
) -> RewriteResult:
    prompt = _load_prompt("20_query_rewrite_retrieval.md")
    if not prompt:
        return fallback

    provider = settings.llm_provider
    system = (
        f"{prompt}\n\nCurrent date: {temporal.current_date}\n"
        f"Current term: {temporal.current_term}\n"
        "Return strict JSON only."
    )
    user = json.dumps(
        {"query": query, "history": history[-6:] if history else []},
        ensure_ascii=False,
    )

    try:
        check_limit_or_raise()

        if provider == "openai":
            import openai

            client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
            resp = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or ""
            if resp.usage:
                record_usage(settings.openai_model, resp.usage.prompt_tokens, "input")
                record_usage(settings.openai_model, resp.usage.completion_tokens, "output")
        elif provider == "anthropic":
            import anthropic

            client = anthropic.AsyncAnthropic()
            resp = await client.messages.create(
                model=settings.anthropic_model,
                max_tokens=300,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            content = resp.content[0].text
            if hasattr(resp, "usage"):
                record_usage(settings.anthropic_model, resp.usage.input_tokens, "input")
                record_usage(settings.anthropic_model, resp.usage.output_tokens, "output")
        else:
            return fallback

        parsed = json.loads(content)
        canonical = _normalize_space(parsed.get("canonical_query", ""))
        if not canonical:
            return fallback

        date_sensitive = parsed.get("date_sensitivity", {}).get(
            "is_sensitive", fallback.date_sensitive
        )
        canonical = _resolve_from_history(canonical, history)
        detected_code = _extract_course_code(canonical) or fallback.detected_course_code
        detected_term = _extract_term_name(canonical) or fallback.detected_term_name

        extras: list[str] = []
        lowered = canonical.lower()
        if detected_code and detected_code.lower() not in lowered:
            extras.append(detected_code)
        if date_sensitive and detected_term and detected_term.lower() not in lowered:
            extras.append(detected_term)
        if extras:
            canonical = _normalize_space(f"{canonical} {' '.join(extras)}")

        return RewriteResult(
            original_query=query,
            rewritten_query=canonical,
            current_date=temporal.current_date,
            current_term=temporal.current_term,
            date_sensitive=bool(date_sensitive),
            detected_course_code=detected_code,
            detected_term_name=detected_term,
        )
    except Exception as exc:
        logger.warning("query rewrite llm failed, fallback to rules", error=str(exc))
        return fallback


async def generate_hyde_passage(query: str) -> str | None:
    """Generate a hypothetical document passage that would answer the query.

    Used for HyDE (Hypothetical Document Embeddings) — the embedding of this
    passage is used for vector search instead of the raw query embedding.
    """
    if not settings.rag_enable_hyde:
        return None

    provider = settings.llm_provider
    system = (
        "You are a Georgia Tech information assistant. "
        "Given a student question, write a ~100-word passage that would appear "
        "in an official Georgia Tech document answering this question. "
        "Be specific and factual. Output ONLY the passage, no preamble."
    )

    try:
        check_limit_or_raise()

        if provider == "openai":
            import openai

            client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
            resp = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": query},
                ],
                temperature=0,
                max_tokens=200,
            )
            passage = resp.choices[0].message.content or ""
            if resp.usage:
                record_usage(settings.openai_model, resp.usage.prompt_tokens, "input")
                record_usage(settings.openai_model, resp.usage.completion_tokens, "output")
        elif provider == "anthropic":
            import anthropic

            client = anthropic.AsyncAnthropic()
            resp = await client.messages.create(
                model=settings.anthropic_model,
                max_tokens=200,
                system=system,
                messages=[{"role": "user", "content": query}],
            )
            passage = resp.content[0].text
            if hasattr(resp, "usage"):
                record_usage(settings.anthropic_model, resp.usage.input_tokens, "input")
                record_usage(settings.anthropic_model, resp.usage.output_tokens, "output")
        else:
            return None

        return passage.strip() if passage.strip() else None
    except Exception as exc:
        logger.warning("hyde passage generation failed", error=str(exc))
        return None


async def rewrite_query(
    query: str,
    history: list[dict] | None = None,
    user_term: str | None = None,
) -> RewriteResult:
    """Rewrite user query for retrieval with temporal grounding."""
    temporal = _get_temporal_context()
    fallback = _build_rule_based_query(query, history, user_term, temporal)

    if not settings.rag_enable_query_rewrite:
        return fallback

    mode = (settings.rag_query_rewrite_mode or "rule").lower()
    if mode == "rule":
        return fallback
    if mode == "llm":
        return await _rewrite_with_llm(query, temporal, fallback, history)
    if mode == "auto":
        if _should_force_rule_rewrite(query, fallback):
            return fallback
        # Use LLM only for short/ambiguous questions where rewrite value is highest.
        if len(query.split()) <= 12 or fallback.date_sensitive:
            return await _rewrite_with_llm(query, temporal, fallback, history)
        return fallback

    return fallback
