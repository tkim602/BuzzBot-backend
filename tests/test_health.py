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
    )

    assert ready is True
    assert checks == {
        "database": True,
        "official_documents": True,
        "published_schedule": True,
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
    )

    assert ready is False
    assert checks["published_schedule"] is False
    assert checks["schedule_freshness"] == "EXPIRED"
    assert checks["checkpoint"] is False
