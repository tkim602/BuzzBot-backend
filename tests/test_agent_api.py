import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes.chat import router
from app.api.schemas.chat import Citation
from app.db.session import get_async_session


def test_pdf_citation_accepts_one_based_page_number():
    citation = Citation(url="https://example.gatech.edu/guide.pdf", quote="Exact text", page=4)

    assert citation.page == 4


def test_only_neutral_chat_route_is_registered():
    from app.main import app

    chat_paths = {
        path
        for path, operations in app.openapi()["paths"].items()
        if "post" in operations and path.endswith("/chat")
    }

    assert chat_paths == {"/chat"}


@pytest.mark.asyncio
async def test_chat_invokes_graph_with_thread_and_maps_response(monkeypatch):
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
                "evidence": [
                    {
                        "kind": "document",
                        "text": "Official registration date",
                        "url": "https://registrar.gatech.edu/current-academic-calendar",
                        "title": "Academic Calendar",
                        "fetched_at": "2026-08-20T00:00:00+00:00",
                        "source": "gt-academic-calendar",
                        "metadata": {},
                    }
                ],
                "term_code": "202608",
            }
        )
    )
    build = MagicMock(return_value=graph)
    monkeypatch.setattr("app.api.routes.chat.build_workflow", build)
    monkeypatch.setattr(
        "app.api.routes.chat.enforce_request_guardrails",
        MagicMock(return_value=("client-a", "normalized-query")),
    )

    @asynccontextmanager
    async def free_slot():
        yield

    monkeypatch.setattr("app.api.routes.chat.acquire_chat_slot", free_slot)
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
            "/chat",
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
    assert body["freshness"]["as_of"] == "2026-08-20T00:00:00+00:00"
    assert body["debug"] is None
    state, config = graph.ainvoke.await_args.args
    assert state["user_term"] == "Fall 2026"
    assert state["active_term"] == "202608"
    assert config == {
        "configurable": {
            "thread_id": "portfolio-demo-1",
            "checkpoint_ns": "client:client-a",
        },
        "metadata": {
            "app": "buzzbot",
            "environment": "development",
            "thread_id": "portfolio-demo-1",
        },
        "tags": ["buzzbot", "chat"],
    }
    assert build.call_args.kwargs["checkpointer"] is app.state.checkpointer


@pytest.mark.asyncio
async def test_chat_debug_payload_is_opt_in(monkeypatch):
    graph = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "intent": "policy",
                "answer": "Grounded answer.",
                "citations": [],
                "confidence": 0.8,
                "notes": [],
                "evidence": [],
            }
        )
    )
    monkeypatch.setattr("app.api.routes.chat.build_workflow", MagicMock(return_value=graph))
    monkeypatch.setattr(
        "app.api.routes.chat.enforce_request_guardrails",
        MagicMock(return_value=("client-a", "query")),
    )
    monkeypatch.setattr("app.api.routes.chat.settings.chat_debug_responses", True)

    @asynccontextmanager
    async def free_slot():
        yield

    monkeypatch.setattr("app.api.routes.chat.acquire_chat_slot", free_slot)
    app = FastAPI()
    app.include_router(router)
    app.state.checkpointer = None

    async def session_override():
        yield object()

    app.dependency_overrides[get_async_session] = session_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/chat", json={"query": "A policy question"})

    assert response.status_code == 200
    assert response.json()["debug"]["intent"] == "policy"


@pytest.mark.asyncio
async def test_chat_rejects_unbounded_or_unsafe_thread_id():
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/chat",
            json={"query": "CS 7650", "thread_id": "invalid thread/id"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_application_adds_request_id_to_response():
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/live")

    assert response.status_code == 200
    assert len(response.headers["X-Request-ID"]) == 8


@pytest.mark.asyncio
async def test_application_lifespan_preloads_reranker(monkeypatch):
    import app.main as main_module

    preload = MagicMock()
    monkeypatch.setattr(main_module, "preload_cross_encoder", preload)
    monkeypatch.setattr(main_module.settings, "rag_enable_reranking", True)
    monkeypatch.setattr(main_module.settings, "langgraph_checkpoint_enabled", False)

    async with main_module.lifespan(FastAPI()):
        pass

    preload.assert_called_once_with()


@pytest.mark.asyncio
async def test_application_lifespan_owns_background_sync_task(monkeypatch):
    import app.main as main_module

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def sync_loop():
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(main_module, "background_sync_loop", sync_loop)
    monkeypatch.setattr(main_module.settings, "background_sync_enabled", True)
    monkeypatch.setattr(main_module.settings, "rag_enable_reranking", False)
    monkeypatch.setattr(main_module.settings, "langgraph_checkpoint_enabled", False)

    async with main_module.lifespan(FastAPI()):
        await asyncio.wait_for(started.wait(), timeout=1)

    assert cancelled.is_set()
