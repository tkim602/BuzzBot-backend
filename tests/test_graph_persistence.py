from unittest.mock import AsyncMock, MagicMock

import pytest

from app.graph.persistence import checkpoint_url, postgres_checkpointer


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
async def test_postgres_checkpointer_sets_up_and_closes(monkeypatch):
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
    saver.setup.assert_awaited_once_with()
    context.__aexit__.assert_awaited_once()
