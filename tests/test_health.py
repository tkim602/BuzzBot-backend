from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.api.routes.health import readiness_status


@pytest.mark.asyncio
async def test_readiness_requires_db_documents_and_nonexpired_schedule():
    now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    session = AsyncMock()
    session.scalar.side_effect = [1, 3, now - timedelta(hours=2)]

    ready, checks = await readiness_status(
        session,
        checkpoint_enabled=True,
        checkpoint_available=True,
        now=now,
        strict=False,
    )

    assert ready is True
    assert checks == {
        "database": True,
        "official_documents": True,
        "official_document_count": 3,
        "official_document_completeness": True,
        "published_schedule": True,
        "schedule_completeness": True,
        "schedule_freshness": "CURRENT",
        "checkpoint": True,
    }


@pytest.mark.asyncio
async def test_readiness_fails_closed_for_expired_schedule_or_checkpoint():
    now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    session = AsyncMock()
    session.scalar.side_effect = [1, 3, now - timedelta(hours=25)]

    ready, checks = await readiness_status(
        session,
        checkpoint_enabled=True,
        checkpoint_available=False,
        now=now,
        strict=False,
    )

    assert ready is False
    assert checks["published_schedule"] is False
    assert checks["schedule_freshness"] == "EXPIRED"
    assert checks["checkpoint"] is False


@pytest.mark.asyncio
async def test_strict_readiness_requires_completed_active_term_manifest():
    now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    session = AsyncMock()
    session.scalar.side_effect = [1, 24, now - timedelta(hours=2), "run-id"]

    ready, checks = await readiness_status(
        session,
        checkpoint_enabled=True,
        checkpoint_available=True,
        now=now,
        strict=True,
        active_term="202608",
        min_official_documents=20,
    )

    assert ready is True
    assert checks["official_document_completeness"] is True
    assert checks["schedule_completeness"] is True


@pytest.mark.asyncio
async def test_strict_readiness_rejects_partial_or_failed_manifest():
    now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    session = AsyncMock()
    session.scalar.side_effect = [1, 24, now - timedelta(hours=2), None]

    ready, checks = await readiness_status(
        session,
        checkpoint_enabled=True,
        checkpoint_available=True,
        now=now,
        strict=True,
        active_term="202608",
        min_official_documents=20,
    )

    assert ready is False
    assert checks["schedule_completeness"] is False


@pytest.mark.asyncio
async def test_strict_readiness_rejects_missing_document_coverage():
    now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    session = AsyncMock()
    session.scalar.side_effect = [1, 4, now - timedelta(hours=2), "run-id"]

    ready, checks = await readiness_status(
        session,
        checkpoint_enabled=False,
        checkpoint_available=False,
        now=now,
        strict=True,
        active_term="202608",
        min_official_documents=20,
    )

    assert ready is False
    assert checks["official_document_count"] == 4
    assert checks["official_document_completeness"] is False
