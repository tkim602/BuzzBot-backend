from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.core import background_sync


@pytest.fixture(autouse=True)
def available_job_lock(monkeypatch):
    @contextmanager
    def available(_job: str):
        yield True

    monkeypatch.setattr(background_sync, "_job_lock", available)


def test_document_sync_is_due_only_after_the_configured_interval(monkeypatch):
    monkeypatch.setattr(background_sync.settings, "document_sync_interval_seconds", 604800)
    now = datetime(2026, 8, 25, tzinfo=UTC)

    assert background_sync.document_sync_due(None, now) is True
    assert background_sync.document_sync_due(now - timedelta(days=8), now) is True
    assert background_sync.document_sync_due(now - timedelta(days=6), now) is False


@pytest.mark.asyncio
async def test_cycle_syncs_active_term_and_due_documents(monkeypatch):
    schedule = AsyncMock()
    documents = AsyncMock()
    monkeypatch.setattr(background_sync, "_sync_schedule", schedule)
    monkeypatch.setattr(background_sync, "_sync_documents", documents)
    monkeypatch.setattr(background_sync, "_latest_document_completion", lambda: None)

    await background_sync.run_sync_cycle(datetime(2026, 8, 25, tzinfo=UTC))

    schedule.assert_awaited_once_with()
    documents.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cycle_skips_recent_document_sync(monkeypatch):
    now = datetime(2026, 8, 25, tzinfo=UTC)
    schedule = AsyncMock()
    documents = AsyncMock()
    monkeypatch.setattr(background_sync, "_sync_schedule", schedule)
    monkeypatch.setattr(background_sync, "_sync_documents", documents)
    monkeypatch.setattr(
        background_sync,
        "_latest_document_completion",
        lambda: now - timedelta(days=2),
    )

    await background_sync.run_sync_cycle(now)

    schedule.assert_awaited_once_with()
    documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_held_advisory_lock_skips_work(monkeypatch):
    @contextmanager
    def held_lock(_job: str):
        yield False

    operation = AsyncMock()
    monkeypatch.setattr(background_sync, "_job_lock", held_lock)

    await background_sync._run_locked("schedule", operation)

    operation.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_failure_isolated_from_server_loop(monkeypatch):
    @contextmanager
    def available_lock(_job: str):
        yield True

    async def fail():
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(background_sync, "_job_lock", available_lock)

    assert await background_sync._run_locked("schedule", fail) is None
