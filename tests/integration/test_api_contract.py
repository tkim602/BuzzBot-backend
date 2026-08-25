from __future__ import annotations

import os
from collections.abc import Mapping
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.auth import get_token_verifier
from app.main import app

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="set RUN_DB_TESTS=1 for PostgreSQL tests"
)


@pytest.mark.asyncio
async def test_fastapi_postgres_and_authenticated_chat_contract(monkeypatch):
    graph = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "intent": "policy",
                "answer": "Deterministic contract answer.",
                "citations": [],
                "confidence": 0.8,
                "notes": [],
                "evidence": [],
            }
        )
    )
    monkeypatch.setattr("app.api.routes.chat.build_workflow", MagicMock(return_value=graph))
    monkeypatch.setattr("app.core.guardrails._limiter.enforce", MagicMock())

    async def verifier(token: str) -> Mapping[str, object]:
        assert token == "test-token"
        return {"uid": "integration-user", "email_verified": False}

    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.state.checkpointer = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            readiness = await client.get("/ready")
            response = await client.post(
                "/chat",
                headers={"Authorization": "Bearer test-token"},
                json={"query": "Contract check", "thread_id": "contract-thread"},
            )
    finally:
        app.dependency_overrides.pop(get_token_verifier, None)

    assert readiness.json()["checks"]["database"] is True
    assert "X-Request-ID" in readiness.headers
    assert response.status_code == 200
    assert response.json()["answer"] == "Deterministic contract answer."
    assert "X-Request-ID" in response.headers
    assert graph.ainvoke.await_args.args[1]["configurable"] == {
        "thread_id": "contract-thread",
        "checkpoint_ns": "client:firebase:integration-user",
    }
