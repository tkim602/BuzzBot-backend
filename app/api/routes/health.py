"""Health and stats endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.usage import get_usage
from app.db.models import Chunk, DataVersion, Document, Source, SourceSnapshot
from app.db.session import get_async_session
from app.retrieval.documents import OFFICIAL_SOURCE_NAMES
from ingestion.schedule.validate import FreshnessState, freshness_state

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "buzzbot"}


@router.get("/live")
async def live():
    return {"status": "ok", "service": "buzzbot"}


async def readiness_status(
    session: AsyncSession,
    *,
    checkpoint_enabled: bool,
    checkpoint_available: bool,
    now: datetime | None = None,
) -> tuple[bool, dict[str, object]]:
    checked_at = now or datetime.now(UTC)
    database_ok = (await session.scalar(text("SELECT 1"))) == 1
    document_chunks = await session.scalar(
        select(func.count())
        .select_from(Chunk)
        .join(Source, Source.id == Chunk.source_id)
        .where(Source.name.in_(OFFICIAL_SOURCE_NAMES))
    )
    latest_schedule = await session.scalar(
        select(func.max(SourceSnapshot.fetched_at))
        .join(DataVersion, DataVersion.id == SourceSnapshot.data_version_id)
        .where(DataVersion.status == "PUBLISHED")
    )
    schedule_freshness = (
        freshness_state(latest_schedule, checked_at)
        if latest_schedule is not None
        else FreshnessState.EXPIRED
    )
    schedule_ok = latest_schedule is not None and schedule_freshness is not FreshnessState.EXPIRED
    checkpoint_ok = not checkpoint_enabled or checkpoint_available
    checks: dict[str, object] = {
        "database": database_ok,
        "official_documents": bool(document_chunks),
        "published_schedule": schedule_ok,
        "schedule_freshness": schedule_freshness.value,
        "checkpoint": checkpoint_ok,
    }
    return all(
        value is True for key, value in checks.items() if key != "schedule_freshness"
    ), checks


@router.get("/ready")
async def ready(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_async_session)],
):
    try:
        is_ready, checks = await readiness_status(
            session,
            checkpoint_enabled=settings.langgraph_checkpoint_enabled,
            checkpoint_available=getattr(request.app.state, "checkpointer", None) is not None,
        )
    except Exception:
        is_ready = False
        checks = {
            "database": False,
            "official_documents": False,
            "published_schedule": False,
            "schedule_freshness": "UNKNOWN",
            "checkpoint": not settings.langgraph_checkpoint_enabled,
        }
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={"status": "ready" if is_ready else "not_ready", "checks": checks},
    )


@router.get("/stats")
async def stats(session: Annotated[AsyncSession, Depends(get_async_session)]):
    """Basic ingestion and index stats."""
    try:
        sources_count = await session.scalar(select(func.count()).select_from(Source))
        docs_count = await session.scalar(select(func.count()).select_from(Document))
        chunks_count = await session.scalar(select(func.count()).select_from(Chunk))

        return {
            "sources": sources_count or 0,
            "documents": docs_count or 0,
            "chunks": chunks_count or 0,
        }
    except Exception:
        return {"sources": 0, "documents": 0, "chunks": 0, "note": "database not initialized"}


@router.get("/usage")
async def usage():
    """Get current API usage stats."""
    data = get_usage()
    return {
        "total_cost": round(data["total_cost"], 4),
        "limit": data["limit"],
        "remaining": round(max(0, data["limit"] - data["total_cost"]), 4),
        "usage_percent": round((data["total_cost"] / data["limit"]) * 100, 1)
        if data["limit"] > 0
        else 0,
        "created_at": data.get("created_at"),
    }
