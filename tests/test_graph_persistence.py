from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.graph.persistence import checkpoint_url, postgres_checkpointer


def test_checkpoint_migration_adopts_tables_created_by_older_startup():
    migration = Path("migrations/versions/006_langgraph_checkpoints.py").read_text()

    assert "CREATE TABLE IF NOT EXISTS checkpoints" in migration
    assert "CREATE TABLE IF NOT EXISTS checkpoint_blobs" in migration
    assert "CREATE TABLE IF NOT EXISTS checkpoint_writes" in migration
    assert "ON CONFLICT DO NOTHING" in migration


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (
            "postgresql+asyncpg://user:pass@db:5432/buzzbot",
            "postgresql://user:pass@db:5432/buzzbot",
        ),
        (
            "postgresql+psycopg://user:pass@db:5432/buzzbot",
            "postgresql://user:pass@db:5432/buzzbot",
        ),
        (
            "postgresql://user:pass@db:5432/buzzbot",
            "postgresql://user:pass@db:5432/buzzbot",
        ),
    ],
)
def test_checkpoint_url_uses_psycopg_compatible_scheme(configured, expected):
    assert checkpoint_url(configured) == expected


@pytest.mark.asyncio
async def test_postgres_checkpointer_does_not_run_schema_ddl_at_startup(monkeypatch):
    saver = MagicMock()
    saver.setup = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = saver
    factory = MagicMock(return_value=context)
    monkeypatch.setattr(
        "app.graph.persistence.AsyncPostgresSaver.from_conn_string",
        factory,
    )

    async with postgres_checkpointer("postgresql+asyncpg://user:pass@db/buzzbot") as active:
        assert active is saver

    factory.assert_called_once_with("postgresql://user:pass@db/buzzbot")
    saver.setup.assert_not_awaited()
    context.__aexit__.assert_awaited_once()
