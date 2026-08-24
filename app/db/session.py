"""Database session management."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

async_engine = create_async_engine(settings.database_url, echo=False, pool_size=10, max_overflow=20)
AsyncSessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

sync_engine = create_engine(settings.database_url_sync, echo=False)
SyncSessionLocal = sessionmaker(sync_engine, class_=Session)


async def get_async_session():
    """Yield an async DB session (for FastAPI dependency injection)."""
    async with AsyncSessionLocal() as session:
        yield session
