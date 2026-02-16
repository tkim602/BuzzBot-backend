"""Chat API endpoint."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ChatResponse:
    """Main chat endpoint — RAG pipeline."""
    query = request.query
    has_rmp = bool(request.rmp_excerpt)

    # 1. Route
    route_result = classify_query(query, has_rmp_excerpt=has_rmp)
    logger.info(
        "query routed",
        intent=route_result.intent,
        freshness=route_result.freshness_strategy,
    )

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

    # 4. Generate answer
    user_ctx = request.user_context.model_dump() if request.user_context else None
    raw_answer = await generate_answer(
        query=query,
        chunks=chunks,
        intent=route_result.intent,
        rmp_excerpt=request.rmp_excerpt,
        user_context=user_ctx,
    )

    # 5. Grounding check
    raw_citations = raw_answer.get("citations", [])
    valid_citations, grounding_notes = check_grounding(raw_citations, chunks)

    # If too many citations were removed, try regeneration once
    if len(raw_citations) > 0 and len(valid_citations) == 0:
        logger.warning("all citations ungrounded, regenerating once")
        raw_answer = await generate_answer(
            query=query,
            chunks=chunks,
            intent=route_result.intent,
            rmp_excerpt=request.rmp_excerpt,
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
    return ChatResponse(
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
