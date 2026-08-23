from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.guardrails import (
    GuardrailViolation,
    acquire_chat_slot,
    enforce_request_guardrails,
)
from app.core.usage import UsageLimitExceeded, get_usage
from app.graph.state import AgentState, CitationItem, EvidenceItem
from app.graph.workflow import WorkflowServices, build_workflow
from app.schemas.chat import (
    AgentChatRequest,
    AgentChatResponse,
    Citation,
    DebugInfo,
    FreshnessInfo,
)
from db.session import get_async_session

router = APIRouter(prefix="/v2", tags=["agentic-rag"])


@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(
    payload: AgentChatRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> AgentChatResponse:
    started = time.perf_counter()
    thread_id = payload.thread_id or str(uuid.uuid4())
    try:
        client_id, _ = enforce_request_guardrails(request, payload.query)
        checkpointer = getattr(request.app.state, "checkpointer", None)
        graph = build_workflow(WorkflowServices(session), checkpointer=checkpointer)
        user_term = payload.user_context.term if payload.user_context else None
        initial_state: AgentState = {
            "query": payload.query,
            "history": [turn.model_dump() for turn in payload.history],
            "user_term": user_term,
        }
        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": f"client:{client_id}",
            }
        }
        async with acquire_chat_slot():
            result = cast(AgentState, await graph.ainvoke(initial_state, config))

        evidence = cast(list[EvidenceItem], result.get("evidence", []))
        citations = [
            Citation(
                url=item["url"],
                title=item.get("title"),
                fetched_at=item.get("fetched_at"),
                quote=item["quote"],
                page=item.get("page"),
            )
            for item in cast(list[CitationItem], result.get("citations", []))
        ]
        sources = list(dict.fromkeys(item["source"] for item in evidence))
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return AgentChatResponse(
            thread_id=thread_id,
            answer=result.get("answer", "I wasn't able to produce a grounded answer."),
            citations=citations,
            confidence=result.get("confidence", 0.2),
            freshness=FreshnessInfo(
                strategy="langgraph_controlled",
                as_of=datetime.now(UTC).isoformat(),
            ),
            notes=result.get("notes", []),
            debug=DebugInfo(
                intent=result.get("intent"),
                source_filter=None,
                retrieval_top_k=len(evidence),
                top_sources=sources,
                rewritten_query=payload.query,
                current_term=result.get("term_code"),
                stage_timings_ms={"total_ms": elapsed_ms},
            ),
        )
    except GuardrailViolation as exc:
        detail: dict[str, object] = {
            "error": "guardrail_violation",
            "message": exc.message,
        }
        if exc.retry_after_seconds is not None:
            detail["retry_after_seconds"] = exc.retry_after_seconds
        raise HTTPException(status_code=429, detail=detail) from exc
    except UsageLimitExceeded as exc:
        usage = get_usage()
        raise HTTPException(
            status_code=429,
            detail={
                "error": "usage_limit_exceeded",
                "message": "The configured API cost limit has been reached.",
                "total_cost": usage["total_cost"],
                "limit": usage["limit"],
            },
        ) from exc
