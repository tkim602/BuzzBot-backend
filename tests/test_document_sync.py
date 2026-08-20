from contextlib import nullcontext
from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest

from app.core.config import settings
from ingestion.documents.registry import DocumentSource
from ingestion.documents.sync import (
    DocumentSyncOutcome,
    FetchedDocument,
    _edition,
    _store_document,
    sync_document_source,
    sync_document_url,
)
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


def _calendar_source() -> DocumentSource:
    return DocumentSource(
        "gt-academic-calendar",
        "academic_calendar",
        "academic_calendar",
        ("https://registrar.gatech.edu/",),
        ("https://registrar.gatech.edu/current-academic-calendar",),
        5,
    )


def _calendar_page() -> str:
    return """
    <html><title>Current Academic Calendar</title><body>
      <select id="academic-year">
        <option value="2026-2027" selected>2026-2027</option>
      </select>
      Registration, classes, examinations, grades, graduation, holidays,
      payment deadlines, faculty deadlines, thesis deadlines, and recess dates.
    </body></html>
    """


def _calendar_rows(count: int = 25) -> list[dict[str, object]]:
    return [
        {
            "id": str(index),
            "date": "August 17 (Mon)",
            "semester": "8",
            "year": "2026",
            "category": "Registration",
            "event": "<p>Registration opens.</p>",
            "weight": index,
        }
        for index in range(count)
    ]


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
    assert isinstance(fetched, FetchedDocument)
    assert fetched.etag == '"v1"'
    assert fetched.source_url == "https://registrar.gatech.edu/registration"


@pytest.mark.asyncio
async def test_one_url_sync_fetches_once_and_uses_canonical_citation(monkeypatch):
    calls: list[httpx.Request] = []
    captured: dict[str, object] = {}
    url = "https://registrar.gatech.edu/registration/holds/#details"
    html = "<html><title>Holds</title><body>" + "official holds policy " * 20 + "</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text=html, request=request)

    def fake_store(session, source, fetched, embed_fn):
        captured["fetched"] = fetched
        return True, 1

    monkeypatch.setattr("ingestion.documents.sync._store_document", fake_store)
    session = MagicMock()
    session.scalar.return_value = None
    result = await sync_document_url(
        _source(),
        url,
        lambda: nullcontext(session),
        lambda texts: [[0.0] * 1536 for _ in texts],
        httpx.MockTransport(handler),
    )

    assert len(calls) == result.requests_used == 1
    assert result.outcome is DocumentSyncOutcome.INDEXED
    fetched = captured["fetched"]
    assert isinstance(fetched, FetchedDocument)
    assert fetched.canonical_url == "https://registrar.gatech.edu/registration/holds"


@pytest.mark.asyncio
async def test_one_url_sync_rejects_outside_allowlist_without_request():
    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("disallowed URL must not be requested")

    result = await sync_document_url(
        _source(),
        "https://example.com/registration/holds",
        lambda: nullcontext(MagicMock()),
        lambda texts: [],
        httpx.MockTransport(forbidden),
    )

    assert result.outcome is DocumentSyncOutcome.FETCH_FAILED
    assert result.requests_used == 0
    assert result.reason == "URL_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_one_url_sync_reports_retry_after_without_retrying_itself():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "30"}, request=request)

    session = MagicMock()
    session.scalar.return_value = None
    result = await sync_document_url(
        _source(),
        _source().seed_urls[0],
        lambda: nullcontext(session),
        lambda texts: [],
        httpx.MockTransport(handler),
    )

    assert calls == result.requests_used == 1
    assert result.outcome is DocumentSyncOutcome.RATE_LIMITED
    assert result.retry_after_seconds == 30


@pytest.mark.asyncio
async def test_one_url_sync_follows_one_safe_canonical_redirect(monkeypatch):
    calls: list[str] = []
    html = (
        "<html><title>Accounting</title><body>" + "official course catalog " * 20 + "</body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/coursesaz/acct":
            return httpx.Response(301, headers={"Location": "/coursesaz/acct/"}, request=request)
        return httpx.Response(200, text=html, request=request)

    source = DocumentSource(
        "gt-catalog",
        "course_catalog",
        "catalog",
        ("https://catalog.gatech.edu/coursesaz/",),
        ("https://catalog.gatech.edu/coursesaz/",),
        150,
    )
    session = MagicMock()
    session.scalar.return_value = None
    monkeypatch.setattr("ingestion.documents.sync._store_document", lambda *args: (True, 1))

    result = await sync_document_url(
        source,
        "https://catalog.gatech.edu/coursesaz/acct",
        lambda: nullcontext(session),
        lambda texts: [],
        httpx.MockTransport(handler),
    )

    assert calls == [
        "https://catalog.gatech.edu/coursesaz/acct",
        "https://catalog.gatech.edu/coursesaz/acct/",
    ]
    assert result.outcome is DocumentSyncOutcome.INDEXED
    assert result.requests_used == 2


@pytest.mark.asyncio
async def test_one_url_sync_never_follows_auth_redirect():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://sso.gatech.edu/login"},
            request=request,
        )

    session = MagicMock()
    session.scalar.return_value = None
    result = await sync_document_url(
        _source(),
        _source().seed_urls[0],
        lambda: nullcontext(session),
        lambda texts: [],
        httpx.MockTransport(handler),
    )

    assert len(calls) == result.requests_used == 1
    assert result.outcome is DocumentSyncOutcome.AUTH_REQUIRED


@pytest.mark.asyncio
async def test_calendar_sync_fetches_official_proxy_with_public_xhr_headers(
    monkeypatch: pytest.MonkeyPatch,
):
    requests: list[httpx.Request] = []
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/current-academic-calendar":
            return httpx.Response(
                200,
                text=_calendar_page(),
                headers={"Content-Type": "text/html"},
                request=request,
            )
        return httpx.Response(
            200,
            json={"data": _calendar_rows()},
            request=request,
        )

    def fake_store(session, source, fetched, embed_fn):
        captured["fetched"] = fetched
        return True, 8

    monkeypatch.setattr("ingestion.documents.sync._store_document", fake_store)
    session = MagicMock()
    session.scalar.return_value = None

    result = await sync_document_source(
        _calendar_source(),
        lambda: nullcontext(session),
        lambda texts: [[0.0] * 1536 for _ in texts],
        httpx.MockTransport(handler),
    )

    assert [request.url.path for request in requests] == [
        "/current-academic-calendar",
        "/calevents/proxy",
    ]
    assert dict(requests[1].url.params) == {"year": "2026-2027", "status": "current"}
    assert requests[1].headers["X-Requested-With"] == "XMLHttpRequest"
    assert requests[1].headers["Referer"] == (
        "https://registrar.gatech.edu/current-academic-calendar"
    )
    assert requests[1].headers["User-Agent"].startswith("Mozilla/5.0")
    assert result.requests_used == 2
    assert result.outcome is DocumentSyncOutcome.INDEXED
    assert result.chunks_indexed == 8
    fetched = captured["fetched"]
    assert isinstance(fetched, FetchedDocument)
    assert fetched.canonical_url == ("https://registrar.gatech.edu/current-academic-calendar")
    assert fetched.edition == "2026-2027"
    assert "Event: Registration opens." in fetched.text


@pytest.mark.asyncio
async def test_invalid_calendar_payload_never_opens_database_or_embeds():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/current-academic-calendar":
            return httpx.Response(
                200,
                text=_calendar_page(),
                headers={"Content-Type": "text/html"},
                request=request,
            )
        return httpx.Response(200, json={"data": _calendar_rows(24)}, request=request)

    def forbidden_session():
        raise AssertionError("database must not open for an invalid calendar payload")

    def forbidden_embed(texts):
        raise AssertionError("invalid calendar payload must not be embedded")

    result = await sync_document_source(
        _calendar_source(),
        forbidden_session,
        forbidden_embed,
        httpx.MockTransport(handler),
    )

    assert result.outcome is DocumentSyncOutcome.EXTRACT_FAILED
    assert result.requests_used == 2
    assert result.reason == "TOO_FEW_EVENTS"


def test_calendar_store_uses_short_fact_chunk_threshold(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}
    source = _calendar_source()
    fetched = FetchedDocument(
        source_url=("https://registrar.gatech.edu/calevents/proxy?year=2026-2027&status=current"),
        canonical_url=source.seed_urls[0],
        title="Georgia Tech Academic Calendar 2026-2027",
        text=("## Georgia Tech Academic Calendar 2026-2027 — Event 1\nEvent: Classes begin."),
        fetched_at=datetime.now(UTC),
        etag=None,
        last_modified=None,
        edition="2026-2027",
    )
    session = MagicMock()
    session.scalar.return_value = None

    monkeypatch.setattr("ingestion.documents.sync.upsert_source", lambda *args: "source-id")
    monkeypatch.setattr("ingestion.documents.sync.upsert_document", lambda *args: "doc-id")

    def fake_chunk_text(text, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("ingestion.documents.sync.chunk_text", fake_chunk_text)
    monkeypatch.setattr("ingestion.documents.sync.index_chunks", lambda *args: 0)
    monkeypatch.setattr("ingestion.documents.sync.update_fetch_state", lambda *args: None)

    _store_document(session, source, fetched, lambda texts: [])

    assert captured["min_chunk_size"] == 10
