"""Chat API endpoint."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.config import settings
from app.core.guardrails import (
    GuardrailViolation,
    acquire_chat_slot,
    enforce_request_guardrails,
    get_client_fingerprint,
    normalize_query,
)
from app.core.usage import UsageLimitExceeded, get_usage
from app.rag.answerer import generate_answer
from app.rag.grounding import check_binary_polarity, check_claim_support, check_grounding
from app.rag.live_fetch import live_fetch_for_query
from app.rag.query_rewrite import generate_hyde_passage, rewrite_query
from app.rag.retrieval import (
    RetrievedChunk,
    get_query_embedding,
    hybrid_retrieve,
    rerank_chunks_by_embedding,
)
from app.rag.router import classify_query
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    Citation,
    DebugInfo,
    FreshnessInfo,
)
from db.session import get_async_session

logger = structlog.get_logger(__name__)

router = APIRouter()
_response_cache = TTLCache[dict](
    max_size=settings.rag_response_cache_max_size,
    ttl_seconds=settings.rag_response_cache_ttl_seconds,
)
FACTUAL_INTENTS = {
    "registrar_calendar",
    "admissions_deadline",
    "catalog_course",
    "course_schedule_sections",
    "policy",
}
DATE_HINT_RE = re.compile(
    r"\b(?:"
    r"\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}/\d{1,2}/\d{4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?"
    r")\b",
    re.IGNORECASE,
)
TERM_MARKER_RE = re.compile(r"\b(?:spring|summer|fall)\s+20\d{2}\b", re.IGNORECASE)
MONTH_MAP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
REGISTRATION_SPECIFIC_TERMS = {
    "add courses",
    "add classes",
    "withdraw",
    "permit",
    "late registration",
    "phase i",
    "phase ii",
    "cross registration",
    "drop",
}
ADMISSIONS_PROGRAM_TERMS = {
    "omscs",
    "mscs",
    "phd",
    "masters",
    "master",
    "graduate",
    "undergraduate",
    "first-year",
    "first year",
    "transfer",
    "online master",
}
OMSCS_FALL_DEADLINE_RE = re.compile(
    r"application deadline for fall matriculation:\s*([A-Za-z]+\s+\d{1,2})",
    re.IGNORECASE,
)
OMSCS_SPRING_DEADLINE_RE = re.compile(
    r"application deadline for spring matriculation:\s*([A-Za-z]+\s+\d{1,2})",
    re.IGNORECASE,
)
MSCS_DEADLINE_RE = re.compile(
    r"application deadline is\s*([A-Za-z]+\s+\d{1,2})",
    re.IGNORECASE,
)


def _response_cache_key(
    normalized_query: str,
    route_intent: str,
    source_filter: str | list[str] | None,
    user_context: dict | None,
    rmp_excerpt: str | None,
) -> str:
    raw = json.dumps(
        {
            "q": normalized_query,
            "intent": route_intent,
            "source": source_filter,
            "user_context": user_context or {},
            "has_rmp_excerpt": bool(rmp_excerpt),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_factual_intent(intent: str, date_sensitive: bool) -> bool:
    return date_sensitive or intent in FACTUAL_INTENTS


def _is_date_question(query: str, date_sensitive: bool) -> bool:
    if date_sensitive:
        return True
    q = query.lower()
    return any(tok in q for tok in ("when", "date", "deadline", "마감", "언제"))


def _extract_date_mentions(text: str) -> set[str]:
    return {m.group(0).strip().lower() for m in DATE_HINT_RE.finditer(text or "")}


def _normalize_date_mention(mention: str) -> str | None:
    m = mention.strip().lower()
    iso = re.fullmatch(r"(20\d{2})-(\d{2})-(\d{2})", m)
    if iso:
        y, mm, dd = iso.groups()
        return f"{y}-{mm}-{dd}"

    slash = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(20\d{2})", m)
    if slash:
        mm, dd, y = slash.groups()
        return f"{int(y):04d}-{int(mm):02d}-{int(dd):02d}"

    named = re.fullmatch(
        r"(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|"
        r"sep|sept|september|oct|october|nov|november|dec|december)\s+(\d{1,2})(?:,\s*(20\d{2}))?",
        m,
    )
    if named:
        month_name, dd, year = named.groups()
        mm = MONTH_MAP.get(month_name)
        if mm is None:
            return None
        if year:
            return f"{int(year):04d}-{mm:02d}-{int(dd):02d}"
        return f"{mm:02d}-{int(dd):02d}"
    return None


def _date_claims_supported(answer: str, citations: list[dict]) -> bool:
    answer_dates = _extract_date_mentions(answer)
    if not answer_dates:
        return True
    quotes_blob = " ".join((c.get("quote") or "").lower() for c in citations)
    quote_dates = _extract_date_mentions(quotes_blob)
    normalized_quote_dates = {d for d in (_normalize_date_mention(x) for x in quote_dates) if d}
    quote_month_day = {d[-5:] for d in normalized_quote_dates if len(d) == 10}
    for answer_date in answer_dates:
        if answer_date in quotes_blob:
            continue
        norm = _normalize_date_mention(answer_date)
        if norm is None:
            return False
        if norm in normalized_quote_dates:
            continue
        if len(norm) == 10 and norm[-5:] in quote_month_day:
            continue
        if len(norm) == 5 and any(d.endswith(norm) for d in normalized_quote_dates):
            continue
        return False
    return True


def _quote_snippet(text: str, center: int, width: int = 220) -> str:
    start = max(0, center - 80)
    end = min(len(text), center + width - 80)
    return " ".join(text[start:end].split())


def _augment_citations_with_date_evidence(
    answer: str,
    citations: list[dict],
    chunks: list[RetrievedChunk],
) -> list[dict]:
    """Attach date-bearing citation quotes from retrieved chunks when missing."""
    if not answer or not chunks:
        return citations

    augmented = list(citations)
    citations_blob = " ".join((c.get("quote") or "").lower() for c in augmented)
    answer_dates = _extract_date_mentions(answer)

    for answer_date in answer_dates:
        if answer_date in citations_blob:
            continue
        target_norm = _normalize_date_mention(answer_date)
        matched = False

        for chunk in chunks:
            chunk_text = chunk.chunk_text or ""
            chunk_lower = chunk_text.lower()
            idx = chunk_lower.find(answer_date)
            if idx < 0 and target_norm:
                for date_match in DATE_HINT_RE.finditer(chunk_text):
                    mention_norm = _normalize_date_mention(date_match.group(0))
                    if mention_norm == target_norm:
                        idx = date_match.start()
                        break
            if idx < 0:
                continue

            augmented.append(
                {
                    "url": chunk.url or "",
                    "title": chunk.title,
                    "fetched_at": chunk.fetched_at,
                    "quote": _quote_snippet(chunk_text, idx),
                }
            )
            citations_blob += " " + (augmented[-1]["quote"] or "").lower()
            matched = True
            break

        if not matched:
            continue

    return augmented


def _merge_unique_chunks(
    primary: list[RetrievedChunk], secondary: list[RetrievedChunk]
) -> list[RetrievedChunk]:
    merged: list[RetrievedChunk] = []
    seen: set[str] = set()
    for chunk in primary + secondary:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        merged.append(chunk)
    return merged


def _top_sources(chunks: list[RetrievedChunk], limit: int = 5) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        name = chunk.source_name or "unknown"
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= limit:
            break
    return names


def _program_specific_evidence_supported(query: str, citations: list[dict]) -> bool:
    q = query.lower()
    if "omscs" not in q and "mscs" not in q:
        return True

    evidence_blob = " ".join(
        " ".join(
            [
                (c.get("url") or ""),
                (c.get("title") or ""),
                (c.get("quote") or ""),
            ]
        )
        for c in citations
    ).lower()

    if "omscs" in q and "omscs" not in evidence_blob:
        return False
    return not (
        "mscs" in q
        and "mscs" not in evidence_blob
        and "master of science in computer science" not in evidence_blob
    )


def _is_ambiguous_registration_deadline_query(query: str, intent: str) -> bool:
    if intent != "registrar_calendar":
        return False
    q = query.lower()
    if "registration deadline" not in q and "deadline to register" not in q:
        return False
    return not any(term in q for term in REGISTRATION_SPECIFIC_TERMS)


def _is_ambiguous_admissions_deadline_query(query: str, intent: str) -> bool:
    if intent != "admissions_deadline":
        return False
    q = query.lower()
    if "application deadline" not in q and "admission deadline" not in q:
        return False
    if "omscs" in q and not TERM_MARKER_RE.search(q):
        return True
    return not any(term in q for term in ADMISSIONS_PROGRAM_TERMS)


def _attempt_admissions_deadline_rule_answer(
    query: str,
    chunks: list[RetrievedChunk],
) -> tuple[dict, list[dict]] | None:
    q = query.lower()

    if "omscs" in q:
        wants_fall = "fall" in q
        wants_spring = "spring" in q
        for chunk in chunks:
            text = chunk.chunk_text or ""
            fall_match = OMSCS_FALL_DEADLINE_RE.search(text)
            spring_match = OMSCS_SPRING_DEADLINE_RE.search(text)
            if wants_fall and fall_match:
                date_str = fall_match.group(1)
                quote = f"Application deadline for Fall matriculation: {date_str}"
                return (
                    {
                        "answer": f"The OMSCS application deadline for Fall matriculation is {date_str}.",
                        "confidence": 0.95,
                        "notes": ["Rule-based deadline extraction from retrieved OMSCS source."],
                    },
                    [
                        {
                            "url": chunk.url or "",
                            "title": chunk.title,
                            "fetched_at": chunk.fetched_at,
                            "quote": quote,
                        }
                    ],
                )
            if wants_spring and spring_match:
                date_str = spring_match.group(1)
                quote = f"Application deadline for Spring matriculation: {date_str}"
                return (
                    {
                        "answer": f"The OMSCS application deadline for Spring matriculation is {date_str}.",
                        "confidence": 0.95,
                        "notes": ["Rule-based deadline extraction from retrieved OMSCS source."],
                    },
                    [
                        {
                            "url": chunk.url or "",
                            "title": chunk.title,
                            "fetched_at": chunk.fetched_at,
                            "quote": quote,
                        }
                    ],
                )
        return None

    if "mscs" in q:
        for chunk in chunks:
            text = chunk.chunk_text or ""
            if (
                "master of science in computer science" not in text.lower()
                and "mscs" not in text.lower()
            ):
                continue
            match = MSCS_DEADLINE_RE.search(text)
            if not match:
                continue
            date_str = match.group(1)
            quote = f"The application deadline is {date_str}"
            return (
                {
                    "answer": (
                        "The application deadline for the Master of Science in Computer Science "
                        f"(MSCS) program is {date_str}."
                    ),
                    "confidence": 0.95,
                    "notes": ["Rule-based deadline extraction from retrieved catalog source."],
                },
                [
                    {
                        "url": chunk.url or "",
                        "title": chunk.title,
                        "fetched_at": chunk.fetched_at,
                        "quote": quote,
                    }
                ],
            )

    return None


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    http_request: Request,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ChatResponse:
    """Main chat endpoint — RAG pipeline."""
    try:
        req_started = time.perf_counter()
        stage_timings_ms: dict[str, int] = {}

        query = payload.query
        history = [h.model_dump() for h in payload.history]
        has_rmp = bool(payload.rmp_excerpt)
        client_id = get_client_fingerprint(http_request)
        user_ctx = payload.user_context.model_dump() if payload.user_context else None
        user_term = user_ctx.get("term") if user_ctx else None

        # 1. Rewrite + Route
        t = time.perf_counter()
        rewrite = await rewrite_query(query, history=history, user_term=user_term)
        stage_timings_ms["rewrite_ms"] = int((time.perf_counter() - t) * 1000)
        retrieval_query = rewrite.rewritten_query
        normalized_query = normalize_query(retrieval_query)

        t = time.perf_counter()
        route_result = classify_query(retrieval_query, has_rmp_excerpt=has_rmp)
        stage_timings_ms["route_ms"] = int((time.perf_counter() - t) * 1000)
        logger.info(
            "query routed",
            intent=route_result.intent,
            freshness=route_result.freshness_strategy,
            client=client_id,
        )

        source_filter: str | list[str] | None = route_result.source_filter
        if route_result.intent == "registrar_calendar" and rewrite.detected_course_code:
            source_filter = ["gt-registrar", "gt-scheduler", "gt-catalog"]

        if _is_ambiguous_registration_deadline_query(query, route_result.intent):
            now = datetime.now(UTC)
            stage_timings_ms["query_embed_ms"] = 0
            stage_timings_ms["retrieve_ms"] = 0
            stage_timings_ms["hyde_ms"] = 0
            stage_timings_ms["live_fetch_ms"] = 0
            stage_timings_ms["answer_ms"] = 0
            stage_timings_ms["grounding_ms"] = 0
            stage_timings_ms["total_ms"] = int((time.perf_counter() - req_started) * 1000)
            return ChatResponse(
                answer=(
                    "Registration has multiple deadlines. Which one do you mean: "
                    "last day to register/add without permit, last day to add with permit, "
                    "or withdrawal deadline? Also include the term (for example, Spring 2026)."
                ),
                citations=[],
                confidence=0.2,
                freshness=FreshnessInfo(
                    strategy=route_result.freshness_strategy, as_of=now.isoformat()
                ),
                notes=["Ambiguous deadline query; clarification requested."],
                debug=DebugInfo(
                    intent=route_result.intent,
                    source_filter=source_filter,
                    live_fetch_used=False,
                    retrieval_top_k=0,
                    top_sources=[],
                    rewritten_query=retrieval_query,
                    current_date=rewrite.current_date,
                    current_term=rewrite.current_term,
                    stage_timings_ms=stage_timings_ms,
                ),
            )

        if _is_ambiguous_admissions_deadline_query(query, route_result.intent):
            now = datetime.now(UTC)
            stage_timings_ms["query_embed_ms"] = 0
            stage_timings_ms["retrieve_ms"] = 0
            stage_timings_ms["hyde_ms"] = 0
            stage_timings_ms["live_fetch_ms"] = 0
            stage_timings_ms["answer_ms"] = 0
            stage_timings_ms["grounding_ms"] = 0
            stage_timings_ms["total_ms"] = int((time.perf_counter() - req_started) * 1000)
            return ChatResponse(
                answer=(
                    "Application deadlines depend on program. Please specify the program "
                    "(for example OMSCS, MSCS, first-year, or transfer) and term."
                ),
                citations=[],
                confidence=0.2,
                freshness=FreshnessInfo(
                    strategy=route_result.freshness_strategy, as_of=now.isoformat()
                ),
                notes=["Ambiguous admissions deadline query; clarification requested."],
                debug=DebugInfo(
                    intent=route_result.intent,
                    source_filter=source_filter,
                    live_fetch_used=False,
                    retrieval_top_k=0,
                    top_sources=[],
                    rewritten_query=retrieval_query,
                    current_date=rewrite.current_date,
                    current_term=rewrite.current_term,
                    stage_timings_ms=stage_timings_ms,
                ),
            )

        use_cache = route_result.freshness_strategy == "indexed" and not has_rmp
        cache_key = _response_cache_key(
            normalized_query=normalized_query,
            route_intent=route_result.intent,
            source_filter=source_filter,
            user_context=user_ctx,
            rmp_excerpt=payload.rmp_excerpt,
        )
        if use_cache:
            cached = _response_cache.get(cache_key)
            if cached is not None:
                logger.info("chat cache hit", intent=route_result.intent, client=client_id)
                return ChatResponse.model_validate(cached)

        # Apply guardrails only for cache misses (expensive path).
        enforce_request_guardrails(http_request, query)

        async with acquire_chat_slot():
            # 2. Retrieve (with optional HyDE embedding)
            t = time.perf_counter()
            query_embedding = await get_query_embedding(retrieval_query)
            stage_timings_ms["query_embed_ms"] = int((time.perf_counter() - t) * 1000)

            hyde_embedding = None
            hyde_used = False
            use_hyde_initial = (
                settings.rag_enable_hyde
                and not rewrite.date_sensitive
                and route_result.intent not in {"registrar_calendar", "admissions_deadline"}
            )
            hyde_elapsed = 0
            if use_hyde_initial:
                t = time.perf_counter()
                hyde_passage = await generate_hyde_passage(retrieval_query)
                if hyde_passage:
                    hyde_embedding = await get_query_embedding(hyde_passage)
                    hyde_used = True
                hyde_elapsed += int((time.perf_counter() - t) * 1000)

            t = time.perf_counter()
            chunks = await hybrid_retrieve(
                session=session,
                query=retrieval_query,
                query_embedding=query_embedding,
                top_k=settings.rag_top_k,
                source_filter=source_filter,
                similarity_threshold=settings.rag_similarity_threshold,
                force_fts=settings.rag_force_fts_for_date_sensitive and rewrite.date_sensitive,
                hyde_embedding=hyde_embedding,
            )
            stage_timings_ms["retrieve_ms"] = int((time.perf_counter() - t) * 1000)

            # HyDE fallback for factual/date-sensitive queries only when initial recall is low.
            if (
                settings.rag_enable_hyde
                and not hyde_used
                and len(chunks) < max(2, settings.rag_top_k // 2)
            ):
                t = time.perf_counter()
                fallback_hyde = await generate_hyde_passage(retrieval_query)
                if fallback_hyde:
                    fallback_hyde_embedding = await get_query_embedding(fallback_hyde)
                    hyde_chunks = await hybrid_retrieve(
                        session=session,
                        query=retrieval_query,
                        query_embedding=query_embedding,
                        top_k=settings.rag_top_k,
                        source_filter=source_filter,
                        similarity_threshold=settings.rag_similarity_threshold,
                        force_fts=settings.rag_force_fts_for_date_sensitive
                        and rewrite.date_sensitive,
                        hyde_embedding=fallback_hyde_embedding,
                    )
                    chunks = _merge_unique_chunks(hyde_chunks, chunks)[: settings.rag_top_k]
                    hyde_used = True
                hyde_elapsed += int((time.perf_counter() - t) * 1000)
            stage_timings_ms["hyde_ms"] = hyde_elapsed

            # 3. Live fetch if needed
            live_fetch_used = False
            if route_result.freshness_strategy in ("live_fetch", "hybrid"):
                t = time.perf_counter()
                live_chunks = await live_fetch_for_query(route_result.intent, retrieval_query)
                if live_chunks:
                    merged = chunks + live_chunks
                    merged = await rerank_chunks_by_embedding(retrieval_query, merged, alpha=0.8)
                    chunks = merged[: max(settings.rag_top_k, settings.live_fetch_max_chunks)]
                    live_fetch_used = True
                stage_timings_ms["live_fetch_ms"] = int((time.perf_counter() - t) * 1000)
            else:
                stage_timings_ms["live_fetch_ms"] = 0

            # Retrieval-empty short circuit: avoid unnecessary LLM cost and hallucinations.
            if not chunks:
                now = datetime.now(UTC)
                stage_timings_ms["answer_ms"] = 0
                stage_timings_ms["grounding_ms"] = 0
                stage_timings_ms["total_ms"] = int((time.perf_counter() - req_started) * 1000)
                response = ChatResponse(
                    answer=(
                        "I couldn't find reliable indexed sources for that question yet. "
                        "Please rephrase with more specific keywords (course code/term) "
                        "or check the official source directly."
                    ),
                    citations=[],
                    confidence=0.2,
                    freshness=FreshnessInfo(
                        strategy=route_result.freshness_strategy,
                        as_of=now.isoformat(),
                    ),
                    notes=["No relevant chunks retrieved."],
                    debug=DebugInfo(
                        intent=route_result.intent,
                        source_filter=source_filter,
                        live_fetch_used=live_fetch_used,
                        retrieval_top_k=0,
                        top_sources=[],
                        rewritten_query=retrieval_query,
                        current_date=rewrite.current_date,
                        current_term=rewrite.current_term,
                        stage_timings_ms=stage_timings_ms,
                    ),
                )
                if use_cache:
                    _response_cache.set(cache_key, response.model_dump())
                return response

            # 4. Generate answer
            t = time.perf_counter()
            raw_answer = await generate_answer(
                query=query,
                chunks=chunks,
                intent=route_result.intent,
                rmp_excerpt=payload.rmp_excerpt,
                user_context=user_ctx,
                current_date=rewrite.current_date,
                current_term=rewrite.current_term,
                history=history,
            )
            stage_timings_ms["answer_ms"] = int((time.perf_counter() - t) * 1000)

            # 5. Grounding check
            t = time.perf_counter()
            raw_citations = raw_answer.get("citations", [])
            valid_citations, grounding_notes = check_grounding(raw_citations, chunks)
            if (
                settings.rag_regenerate_on_ungrounded
                and len(raw_citations) > 0
                and len(valid_citations) == 0
            ):
                logger.warning("all citations ungrounded, regenerating once")
                raw_answer = await generate_answer(
                    query=query,
                    chunks=chunks,
                    intent=route_result.intent,
                    rmp_excerpt=payload.rmp_excerpt,
                    user_context=user_ctx,
                    current_date=rewrite.current_date,
                    current_term=rewrite.current_term,
                    history=history,
                )
                raw_citations = raw_answer.get("citations", [])
                valid_citations, grounding_notes = check_grounding(raw_citations, chunks)

            date_verified = True
            if _is_date_question(query, rewrite.date_sensitive):
                date_verified = _date_claims_supported(
                    raw_answer.get("answer", ""),
                    valid_citations,
                )
                if not date_verified:
                    logger.warning("date claim unsupported, regenerating once")
                    raw_answer = await generate_answer(
                        query=query,
                        chunks=chunks,
                        intent=route_result.intent,
                        rmp_excerpt=payload.rmp_excerpt,
                        user_context=user_ctx,
                        current_date=rewrite.current_date,
                        current_term=rewrite.current_term,
                        history=history,
                    )
                    raw_citations = raw_answer.get("citations", [])
                    valid_citations, grounding_notes = check_grounding(raw_citations, chunks)
                    date_verified = _date_claims_supported(
                        raw_answer.get("answer", ""), valid_citations
                    )
                    if not date_verified:
                        valid_citations = _augment_citations_with_date_evidence(
                            raw_answer.get("answer", ""),
                            valid_citations,
                            chunks,
                        )
                        date_verified = _date_claims_supported(
                            raw_answer.get("answer", ""), valid_citations
                        )
                    if not date_verified:
                        grounding_notes.append(
                            "Answer date could not be verified from citation quotes."
                        )
            stage_timings_ms["grounding_ms"] = int((time.perf_counter() - t) * 1000)

        is_factual = _is_factual_intent(route_result.intent, rewrite.date_sensitive)
        claims_supported = True
        if is_factual:
            claims_supported, claim_notes = await check_claim_support(
                raw_answer.get("answer", ""), chunks
            )
            grounding_notes.extend(claim_notes)
        program_evidence_ok = _program_specific_evidence_supported(query, valid_citations)
        if (
            route_result.intent == "admissions_deadline"
            and is_factual
            and (not valid_citations or not date_verified or not program_evidence_ok)
        ):
            fallback = _attempt_admissions_deadline_rule_answer(query, chunks)
            if fallback:
                raw_answer, valid_citations = fallback
                date_verified = _date_claims_supported(
                    raw_answer.get("answer", ""), valid_citations
                )
                program_evidence_ok = _program_specific_evidence_supported(query, valid_citations)
                claims_supported, claim_notes = await check_claim_support(
                    raw_answer.get("answer", ""), chunks
                )
                grounding_notes.extend(claim_notes)

        polarity_consistent = True
        if is_factual and claims_supported:
            polarity_consistent, polarity_notes = check_binary_polarity(
                raw_answer.get("answer", ""), raw_answer.get("_binary_verdict")
            )
            grounding_notes.extend(polarity_notes)

        if is_factual and (
            not valid_citations
            or not date_verified
            or not program_evidence_ok
            or not claims_supported
            or not polarity_consistent
        ):
            abstain_answer = (
                "I don't have enough grounded evidence in the current retrieved sources to answer "
                "that reliably. Please try a more specific query (program + term) or check the "
                "official Georgia Tech source directly."
            )
            raw_answer["answer"] = abstain_answer
            raw_answer["confidence"] = 0.2
            notes = raw_answer.get("notes", [])
            notes.append(
                "Strict cite-or-abstain policy applied due to insufficient grounded evidence."
            )
            if not program_evidence_ok:
                notes.append("Program-specific evidence was missing for the requested program.")
            raw_answer["notes"] = notes
            valid_citations = []

        # 6. Build response
        citations = [
            Citation(
                url=c.get("url", ""),
                title=c.get("title"),
                fetched_at=c.get("fetched_at"),
                quote=c.get("quote", ""),
            )
            for c in valid_citations
        ]

        notes = raw_answer.get("notes", []) + grounding_notes
        confidence = raw_answer.get("confidence", 0.5)
        if _is_factual_intent(route_result.intent, rewrite.date_sensitive) and not citations:
            confidence = min(confidence, 0.2)

        now = datetime.now(UTC)
        stage_timings_ms["total_ms"] = int((time.perf_counter() - req_started) * 1000)
        response = ChatResponse(
            answer=raw_answer.get("answer", "I wasn't able to generate a good answer."),
            citations=citations,
            confidence=confidence,
            freshness=FreshnessInfo(
                strategy=route_result.freshness_strategy,
                as_of=now.isoformat(),
            ),
            notes=notes,
            debug=DebugInfo(
                intent=route_result.intent,
                source_filter=source_filter,
                live_fetch_used=live_fetch_used,
                retrieval_top_k=len(chunks),
                top_sources=_top_sources(chunks),
                rewritten_query=retrieval_query,
                current_date=rewrite.current_date,
                current_term=rewrite.current_term,
                stage_timings_ms=stage_timings_ms,
            ),
        )
        if use_cache:
            _response_cache.set(cache_key, response.model_dump())
        return response

    except GuardrailViolation as e:
        detail = {
            "error": "guardrail_violation",
            "message": e.message,
        }
        if e.retry_after_seconds:
            detail["retry_after_seconds"] = e.retry_after_seconds
        raise HTTPException(status_code=429, detail=detail) from e

    except UsageLimitExceeded as e:
        usage = get_usage()
        raise HTTPException(
            status_code=429,
            detail={
                "error": "usage_limit_exceeded",
                "message": f"API usage limit exceeded. Current: ${usage['total_cost']:.4f}, Limit: ${usage['limit']:.2f}",
                "total_cost": usage["total_cost"],
                "limit": usage["limit"],
            },
        ) from e
