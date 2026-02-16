"""Health and stats endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

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
