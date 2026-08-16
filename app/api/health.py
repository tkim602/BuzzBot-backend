"""Health and stats endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage import get_usage, set_limit, reset_usage
from db.models import Chunk, Document, Source
from db.session import get_async_session

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "buzzbot"}


@router.get("/stats")
async def stats(session: AsyncSession = Depends(get_async_session)):
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
        "usage_percent": round((data["total_cost"] / data["limit"]) * 100, 1) if data["limit"] > 0 else 0,
        "created_at": data.get("created_at"),
    }


@router.post("/usage/reset")
async def usage_reset():
    """Reset usage tracking (keeps limit)."""
    reset_usage()
    return {"status": "ok", "message": "Usage reset"}


@router.post("/usage/set-limit")
async def usage_set_limit(limit: float):
    """Set new usage limit in dollars."""
    applied_limit = set_limit(limit)
    return {"status": "ok", "limit": applied_limit}
