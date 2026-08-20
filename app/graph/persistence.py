from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


def checkpoint_url(configured_url: str) -> str:
    for scheme in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if configured_url.startswith(scheme):
            return configured_url.replace(scheme, "postgresql://", 1)
    if configured_url.startswith("postgresql://"):
        return configured_url
    raise ValueError("LangGraph checkpointing requires a PostgreSQL URL")


@asynccontextmanager
async def postgres_checkpointer(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(database_url)) as saver:
        await saver.setup()
        yield saver
