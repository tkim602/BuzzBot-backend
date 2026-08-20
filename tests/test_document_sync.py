from contextlib import nullcontext
from unittest.mock import MagicMock

import httpx
import pytest

from app.core.config import settings
from ingestion.documents.registry import DocumentSource
from ingestion.documents.sync import DocumentSyncOutcome, _edition, sync_document_source
from ingestion.index import get_embedding_function


def _source() -> DocumentSource:
    return DocumentSource(
        "gt-registrar",
        "official_policy",
        "registrar",
        ("https://registrar.gatech.edu/",),
        ("https://registrar.gatech.edu/registration",),
        5,
    )


def test_catalog_edition_is_preserved_when_present():
    assert _edition("Georgia Tech 2026-2027 Catalog") == "2026-2027"
    assert _edition("Registration policy") is None


def test_embedding_client_receives_key_loaded_by_settings(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, str] = {}

    class FakeClient:
        def __init__(self, *, api_key: str):
            captured["api_key"] = api_key

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr("openai.OpenAI", FakeClient)

    get_embedding_function()

    assert captured == {"api_key": "test-key"}


@pytest.mark.asyncio
async def test_probe_failure_prevents_fetch_database_and_embedding():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, request=request)

    def forbidden():
        raise AssertionError("database must not open")

    result = await sync_document_source(
        _source(),
        forbidden,
        lambda texts: (_ for _ in ()).throw(AssertionError("must not embed")),
        httpx.MockTransport(handler),
    )

    assert result.outcome is DocumentSyncOutcome.PROBE_FAILED
    assert result.requests_used == calls == 1


@pytest.mark.asyncio
async def test_ready_probe_allows_one_fetch_and_passes_citation_metadata(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[httpx.Request] = []
    html = "<html><title>Registration</title><body>" + "official policy " * 20 + "</body></html>"
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            text=html,
            headers={"Content-Type": "text/html", "ETag": '"v1"'},
            request=request,
        )

    def fake_store(session, source, result, embed_fn):
        captured["source"] = source
        captured["result"] = result
        return True, 2

    monkeypatch.setattr("ingestion.documents.sync._store_document", fake_store)
    session = MagicMock()
    session.scalar.return_value = None
    result = await sync_document_source(
        _source(),
        lambda: nullcontext(session),
        lambda texts: [[0.0] * 1536 for _ in texts],
        httpx.MockTransport(handler),
    )

    assert len(calls) == result.requests_used == 2
    assert result.outcome is DocumentSyncOutcome.INDEXED
    assert result.changed == 1
    assert result.chunks_indexed == 2
    fetched = captured["result"]
    assert fetched.etag == '"v1"'
    assert fetched.source_url == "https://registrar.gatech.edu/registration"
