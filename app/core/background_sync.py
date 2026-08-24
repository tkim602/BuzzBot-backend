"""Bounded periodic refresh using the existing ingestion orchestrators."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, text

from app.core.config import settings
from app.db.models import IngestionRun
from app.db.session import SyncSessionLocal, sync_engine
from ingestion.documents.registry import load_document_sources
from ingestion.documents.sync_all import sync_document_profile
from ingestion.documents.sync_source import PROVIDER as DOCUMENT_PROVIDER
from ingestion.index import get_embedding_function
from ingestion.schedule.sync_term import sync_oscar_term

logger = structlog.get_logger(__name__)
_LOCK_KEYS = {"schedule": 1_118_130_433, "documents": 1_118_130_434}


def document_sync_due(last_completed: datetime | None, now: datetime) -> bool:
    return last_completed is None or now - last_completed >= timedelta(
        seconds=settings.document_sync_interval_seconds
    )


def _latest_document_completion() -> datetime | None:
    with SyncSessionLocal() as session:
        runs = session.scalars(
            select(IngestionRun)
            .where(
                IngestionRun.provider == DOCUMENT_PROVIDER,
                IngestionRun.status == "COMPLETED",
                IngestionRun.completed_at.is_not(None),
            )
            .order_by(IngestionRun.completed_at.desc())
        )
        for run in runs:
            if run.scope_json.get("profile") == "run3":
                return run.completed_at
    return None


@contextmanager
def _job_lock(job: str) -> Iterator[bool]:
    key = _LOCK_KEYS[job]
    with sync_engine.connect() as connection:
        acquired = bool(
            connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        )
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


async def _run_locked(
    job: str, operation: Callable[[], Awaitable[object]]
) -> object | None:
    with _job_lock(job) as acquired:
        if not acquired:
            logger.info("background sync skipped", job=job, reason="LOCKED")
            return None
        try:
            result = await operation()
        except Exception as exc:
            logger.error("background sync failed", job=job, error=type(exc).__name__)
            return None
    logger.info(
        "background sync finished",
        job=job,
        run_id=str(getattr(result, "run_id", "")),
        status=getattr(result, "status", "UNKNOWN"),
    )
    return result


async def _sync_schedule() -> object:
    return await sync_oscar_term(term=settings.active_term_code)


async def _sync_documents() -> object:
    sources = tuple(
        source for source in load_document_sources() if "run3" in source.profiles
    )
    return await sync_document_profile(
        "run3", sources, SyncSessionLocal, get_embedding_function()
    )


async def run_sync_cycle(now: datetime | None = None) -> None:
    current = now or datetime.now(UTC)
    await _run_locked("schedule", _sync_schedule)
    if document_sync_due(_latest_document_completion(), current):
        await _run_locked("documents", _sync_documents)


async def background_sync_loop() -> None:
    while True:
        try:
            await run_sync_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("background sync cycle failed", error=type(exc).__name__)
        await asyncio.sleep(settings.schedule_sync_interval_seconds)
