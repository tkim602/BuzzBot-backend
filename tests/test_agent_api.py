from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.agent import router
from db.session import get_async_session


@pytest.mark.asyncio
async def test_v2_chat_invokes_graph_with_thread_and_maps_response(monkeypatch):
    graph = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "intent": "registration_calendar",
                "answer": "Registration closes on the official date.",
                "citations": [
                    {
                        "url": "https://registrar.gatech.edu/current-academic-calendar",
                        "title": "Academic Calendar",
                        "fetched_at": "2026-08-20T00:00:00+00:00",
                        "quote": "Official registration date",
                    }
                ],
                "confidence": 0.8,
                "notes": [],
                "evidence": [{"source": "gt-academic-calendar"}],
                "term_code": "202608",
            }
        )
    )
    build = MagicMock(return_value=graph)
    monkeypatch.setattr("app.api.agent.build_workflow", build)
    monkeypatch.setattr("app.api.agent.enforce_request_guardrails", MagicMock())

    @asynccontextmanager
    async def free_slot():
        yield

    monkeypatch.setattr("app.api.agent.acquire_chat_slot", free_slot)
    app = FastAPI()
    app.include_router(router)
    app.state.checkpointer = object()

    async def session_override():
        yield object()

    app.dependency_overrides[get_async_session] = session_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v2/chat",
            json={
                "query": "When is Fall 2026 registration?",
                "thread_id": "portfolio-demo-1",
                "user_context": {"term": "Fall 2026"},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == "portfolio-demo-1"
    assert body["citations"][0]["url"].startswith("https://registrar.gatech.edu/")
    state, config = graph.ainvoke.await_args.args
    assert state["user_term"] == "Fall 2026"
    assert config == {"configurable": {"thread_id": "portfolio-demo-1"}}
    assert build.call_args.kwargs["checkpointer"] is app.state.checkpointer


@pytest.mark.asyncio
async def test_v2_chat_rejects_unbounded_or_unsafe_thread_id():
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v2/chat",
            json={"query": "CS 7650", "thread_id": "invalid thread/id"},
        )

    assert response.status_code == 422
