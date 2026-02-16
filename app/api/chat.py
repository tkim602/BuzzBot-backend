"""Chat API endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

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
from app.rag.grounding import check_grounding
from app.rag.live_fetch import live_fetch_for_query
from app.rag.retrieval import get_query_embedding, hybrid_retrieve
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


def _response_cache_key(
    normalized_query: str,
    route_intent: str,
    source_filter: str | None,
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


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> ChatResponse:
    """Main chat endpoint — RAG pipeline."""
    try:
        query = payload.query
        has_rmp = bool(payload.rmp_excerpt)
        client_id = get_client_fingerprint(http_request)
        normalized_query = normalize_query(query)

        # 1. Route
        route_result = classify_query(query, has_rmp_excerpt=has_rmp)
        logger.info(
            "query routed",
            intent=route_result.intent,
            freshness=route_result.freshness_strategy,
            client=client_id,
        )

        user_ctx = payload.user_context.model_dump() if payload.user_context else None
        use_cache = route_result.freshness_strategy == "indexed" and not has_rmp
        cache_key = _response_cache_key(
            normalized_query=normalized_query,
            route_intent=route_result.intent,
            source_filter=route_result.source_filter,
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
            # 2. Retrieve
            query_embedding = await get_query_embedding(query)
            chunks = await hybrid_retrieve(
                session=session,
                query=query,
                query_embedding=query_embedding,
                top_k=settings.rag_top_k,
                source_filter=route_result.source_filter,
                similarity_threshold=settings.rag_similarity_threshold,
            )

            # 3. Live fetch if needed
            live_fetch_used = False
            if route_result.freshness_strategy in ("live_fetch", "hybrid"):
                live_chunks = await live_fetch_for_query(route_result.intent, query)
                if live_chunks:
                    chunks = live_chunks + chunks  # Prioritize fresh content
                    live_fetch_used = True

            # Retrieval-empty short circuit: avoid unnecessary LLM cost and hallucinations.
            if not chunks:
                now = datetime.now(timezone.utc)
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
                        live_fetch_used=live_fetch_used,
                        retrieval_top_k=0,
                    ),
                )
                if use_cache:
                    _response_cache.set(cache_key, response.model_dump())
                return response

            # 4. Generate answer
            raw_answer = await generate_answer(
                query=query,
                chunks=chunks,
                intent=route_result.intent,
                rmp_excerpt=payload.rmp_excerpt,
                user_context=user_ctx,
            )

            # 5. Grounding check
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
                )
                raw_citations = raw_answer.get("citations", [])
                valid_citations, grounding_notes = check_grounding(raw_citations, chunks)

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

        now = datetime.now(timezone.utc)
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
                live_fetch_used=live_fetch_used,
                retrieval_top_k=len(chunks),
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
        raise HTTPException(status_code=429, detail=detail)

    except UsageLimitExceeded:
        usage = get_usage()
        raise HTTPException(
            status_code=429,
            detail={
                "error": "usage_limit_exceeded",
                "message": f"API usage limit exceeded. Current: ${usage['total_cost']:.4f}, Limit: ${usage['limit']:.2f}",
                "total_cost": usage["total_cost"],
                "limit": usage["limit"],
            }
        )
