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
    _is_recognized_error_page,
    _store_document,
    sync_document_source,
    sync_document_url,
)
from ingestion.extract import ExtractedContent
from ingestion.index import get_embedding_function
from ingestion.normalize import content_hash


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
async def test_one_url_sync_rejects_missing_title_as_quality_failure(monkeypatch):
    html = "<html><body>" + "official policy text " * 30 + "</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    monkeypatch.setattr(
        "ingestion.documents.sync.extract_content",
        lambda url, body: ExtractedContent(url, None, "official policy text " * 30),
    )
    monkeypatch.setattr(
        "ingestion.documents.sync._store_document",
        lambda *args: (_ for _ in ()).throw(AssertionError("quality failure must not store")),
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

    assert result.outcome is DocumentSyncOutcome.EXTRACT_FAILED
    assert result.reason == "QUALITY_GATE_FAILED"


@pytest.mark.asyncio
async def test_one_url_sync_rejects_recognized_login_page(monkeypatch):
    html = """
    <html><title>Georgia Tech Sign In</title><body>
      <form action="/login"><input name="username"><input type="password"></form>
      Sign in with your Georgia Tech account to continue.
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    monkeypatch.setattr(
        "ingestion.documents.sync._store_document",
        lambda *args: (_ for _ in ()).throw(AssertionError("login page must not store")),
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

    assert result.outcome is DocumentSyncOutcome.EXTRACT_FAILED
    assert result.reason == "QUALITY_GATE_FAILED"


def test_recognized_error_titles_do_not_reject_legitimate_error_documentation():
    assert _is_recognized_error_page("<html></html>", "Access Denied")
    assert _is_recognized_error_page("<html></html>", "Attention Required! | Cloudflare")
    assert not _is_recognized_error_page("<html></html>", "Registration Error Messages | Registrar")


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
async def test_one_url_sync_follows_safe_alias_without_duplicate_document(monkeypatch):
    alias = "https://registrar.gatech.edu/registration/registration-information"
    target = "https://registrar.gatech.edu/registration/registration-assistance"
    calls: list[httpx.Request] = []
    captured: dict[str, FetchedDocument] = {}
    html = (
        "<html><title>Registration Assistance</title><body>"
        + "official registration assistance " * 20
        + "</body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if str(request.url) == alias:
            return httpx.Response(301, headers={"Location": target}, request=request)
        return httpx.Response(200, text=html, request=request)

    def fake_store(session, source, fetched, embed_fn):
        captured["fetched"] = fetched
        return False, 3

    monkeypatch.setattr("ingestion.documents.sync._store_document", fake_store)
    session = MagicMock()
    session.scalar.return_value = None

    result = await sync_document_url(
        _source(),
        alias,
        lambda: nullcontext(session),
        lambda texts: [],
        httpx.MockTransport(handler),
    )

    assert [str(request.url) for request in calls] == [alias, target]
    assert result.outcome is DocumentSyncOutcome.UNCHANGED
    assert result.requests_used == 2
    assert captured["fetched"].source_url == target
    assert captured["fetched"].canonical_url == target


@pytest.mark.asyncio
async def test_one_url_sync_rejects_second_redirect():
    alias = "https://registrar.gatech.edu/registration/registration-faqs"
    target = "https://registrar.gatech.edu/registration/registration-faq"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        location = target if str(request.url) == alias else "/registration/third"
        return httpx.Response(301, headers={"Location": location}, request=request)

    session = MagicMock()
    session.scalar.return_value = None
    result = await sync_document_url(
        _source(),
        alias,
        lambda: nullcontext(session),
        lambda texts: [],
        httpx.MockTransport(handler),
    )

    assert calls == [alias, target]
    assert result.outcome is DocumentSyncOutcome.FETCH_FAILED
    assert result.requests_used == 2


@pytest.mark.asyncio
async def test_one_url_sync_rejects_external_redirect_without_following():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            301,
            headers={"Location": "https://example.com/registration"},
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

    assert calls == [_source().seed_urls[0]]
    assert result.outcome is DocumentSyncOutcome.FETCH_FAILED
    assert result.reason == "REDIRECT_NOT_ALLOWED"
    assert result.requests_used == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "start", "target"),
    (
        (
            DocumentSource(
                "gt-omscs",
                "omscs_policy",
                "omscs",
                ("https://omscs.gatech.edu/",),
                ("https://omscs.gatech.edu/degree-requirements",),
                10,
            ),
            "https://omscs.gatech.edu/degree-requirements",
            "https://omscs.gatech.edu/news/foo",
        ),
        (
            DocumentSource(
                "gt-admission",
                "admissions",
                "admissions",
                ("https://admission.gatech.edu/first-year/",),
                ("https://admission.gatech.edu/first-year/foo",),
                30,
            ),
            "https://admission.gatech.edu/first-year/foo",
            "https://admission.gatech.edu/first-year/foo/bar",
        ),
    ),
)
async def test_one_url_sync_rejects_redirect_outside_adapter_scope(source, start, target):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(301, headers={"Location": target}, request=request)

    session = MagicMock()
    session.scalar.return_value = None
    result = await sync_document_url(
        source,
        start,
        lambda: nullcontext(session),
        lambda texts: [],
        httpx.MockTransport(handler),
    )

    assert calls == [start]
    assert result.outcome is DocumentSyncOutcome.FETCH_FAILED
    assert result.reason == "REDIRECT_NOT_ALLOWED"
    assert result.requests_used == 1


@pytest.mark.asyncio
async def test_304_repairs_invalid_chunks_from_trusted_stored_content(monkeypatch):
    source = _source()
    state = MagicMock(etag='"v1"', last_modified=None)
    document = MagicMock(
        content_text="Official registration policy details. " * 80,
        title="Registration",
        etag='"v1"',
        last_modified=None,
        doc_id="doc-id",
    )
    bad_chunk = MagicMock(token_count=1)
    session = MagicMock()
    session.scalar.side_effect = [state, document]
    session.scalars.return_value.all.return_value = [bad_chunk]
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304, request=request)

    def fake_store(session, source, fetched, embed_fn):
        captured["fetched"] = fetched
        return True, 2

    monkeypatch.setattr("ingestion.documents.sync._store_document", fake_store)
    result = await sync_document_url(
        source,
        source.seed_urls[0],
        lambda: nullcontext(session),
        lambda texts: [],
        httpx.MockTransport(handler),
    )

    assert result.outcome is DocumentSyncOutcome.INDEXED
    assert result.requests_used == 1
    assert result.chunks_indexed == 2
    fetched = captured["fetched"]
    assert isinstance(fetched, FetchedDocument)
    assert fetched.text == document.content_text


@pytest.mark.asyncio
async def test_304_reindexes_trusted_content_when_chunking_version_is_stale(monkeypatch):
    source = _source()
    state = MagicMock(etag='"v1"', last_modified=None)
    document = MagicMock(
        content_text="Official registration policy details. " * 80,
        title="Registration",
        etag='"v1"',
        last_modified=None,
        doc_id="doc-id",
        metadata_json={"chunking_version": 1},
    )
    healthy_chunk = MagicMock(token_count=100)
    session = MagicMock()
    session.scalar.side_effect = [state, document]
    session.scalars.return_value.all.return_value = [healthy_chunk]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304, request=request)

    monkeypatch.setattr("ingestion.documents.sync._store_document", lambda *args: (True, 2))
    result = await sync_document_url(
        source,
        source.seed_urls[0],
        lambda: nullcontext(session),
        lambda texts: [],
        httpx.MockTransport(handler),
    )

    assert result.outcome is DocumentSyncOutcome.INDEXED
    assert result.chunks_indexed == 2


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
        return [object()]

    monkeypatch.setattr("ingestion.documents.sync.chunk_text", fake_chunk_text)
    monkeypatch.setattr("ingestion.documents.sync.index_chunks", lambda *args: 1)
    monkeypatch.setattr("ingestion.documents.sync.update_fetch_state", lambda *args: None)

    _store_document(session, source, fetched, lambda texts: [])

    assert captured["min_chunk_size"] == 10


def test_unchanged_document_reindexes_only_when_stored_chunks_are_invalid(monkeypatch):
    source = _source()
    fetched = FetchedDocument(
        source.seed_urls[0],
        source.seed_urls[0],
        "Registration",
        "Official registration policy details. " * 80,
        datetime.now(UTC),
        None,
        None,
        None,
    )
    existing = MagicMock()
    existing.content_hash = content_hash(fetched.text)
    existing.doc_id = "doc-id"
    bad_chunk = MagicMock(token_count=1, metadata_json={})
    session = MagicMock()
    session.scalar.return_value = existing
    session.scalars.return_value.all.return_value = [bad_chunk]

    monkeypatch.setattr("ingestion.documents.sync.upsert_source", lambda *args: "source-id")
    monkeypatch.setattr("ingestion.documents.sync.upsert_document", lambda *args: "doc-id")
    monkeypatch.setattr("ingestion.documents.sync.chunk_text", lambda *args, **kwargs: [object()])
    monkeypatch.setattr("ingestion.documents.sync.index_chunks", lambda *args: 1)
    monkeypatch.setattr("ingestion.documents.sync.update_fetch_state", lambda *args: None)

    changed, indexed = _store_document(session, source, fetched, lambda texts: [])

    assert changed is True
    assert indexed == 1
