import httpx
import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import IngestionRun, IngestionRunUnit
from ingestion.documents.registry import DocumentSource
from ingestion.documents.sync import (
    DocumentSyncOutcome,
    DocumentSyncResult,
)
from ingestion.documents.sync_source import (
    PROVIDER,
    _unit_result,
    sync_document_source_urls,
)
from ingestion.orchestration import UnitOutcome, create_run, plan_run


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    IngestionRun.__table__.create(engine)
    IngestionRunUnit.__table__.create(engine)
    try:
        yield sessionmaker(engine, class_=Session)
    finally:
        engine.dispose()


def _source(max_urls: int = 2) -> DocumentSource:
    return DocumentSource(
        "gt-registrar",
        "official_policy",
        "registrar",
        ("https://registrar.gatech.edu/registration",),
        ("https://registrar.gatech.edu/registration",),
        max_urls,
    )


@pytest.mark.asyncio
async def test_fresh_run_freezes_bounded_urls_and_records_each_result(monkeypatch, sessions):
    html = """
    <a href="/registration/holds">Holds</a>
    <a href="/registration/waitlists">Waitlists beyond cap</a>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    called: list[str] = []

    async def sync_url(source, url, session_factory, embed_fn, transport):
        called.append(url)
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.INDEXED,
            requests_used=1,
            fetched=1,
            changed=1,
            chunks_indexed=3,
        )

    monkeypatch.setattr("ingestion.documents.sync_source.sync_document_url", sync_url)
    summary = await sync_document_source_urls(
        _source(max_urls=3),
        sessions,
        lambda texts: [],
        httpx.MockTransport(handler),
        verification_limit=2,
        concurrency=1,
    )

    assert summary.status == "COMPLETED"
    assert summary.planned_units == (
        "https://registrar.gatech.edu/registration",
        "https://registrar.gatech.edu/registration/holds",
    )
    assert called == list(summary.planned_units)
    with sessions() as session:
        units = session.scalars(
            select(IngestionRunUnit)
            .where(IngestionRunUnit.run_id == summary.run_id)
            .order_by(IngestionRunUnit.position)
        ).all()
    assert [unit.result_json["chunks_indexed"] for unit in units] == [3, 3]


@pytest.mark.asyncio
async def test_production_manifest_includes_every_url_within_ceiling(monkeypatch, sessions):
    html = """
    <a href="/registration/holds">Holds</a>
    <a href="/registration/waitlists">Waitlists</a>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    async def sync_url(source, url, session_factory, embed_fn, transport):
        return DocumentSyncResult(source.name, DocumentSyncOutcome.UNCHANGED, 1, fetched=1)

    monkeypatch.setattr("ingestion.documents.sync_source.sync_document_url", sync_url)
    summary = await sync_document_source_urls(
        _source(max_urls=3),
        sessions,
        lambda texts: [],
        httpx.MockTransport(handler),
        concurrency=1,
    )

    assert summary.status == "COMPLETED"
    assert summary.planned_units == (
        "https://registrar.gatech.edu/registration",
        "https://registrar.gatech.edu/registration/holds",
        "https://registrar.gatech.edu/registration/waitlists",
    )


@pytest.mark.asyncio
async def test_safety_ceiling_fails_planning_before_verification_limit(sessions):
    html = """
    <a href="/registration/holds">Holds</a>
    <a href="/registration/waitlists">Waitlists</a>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    summary = await sync_document_source_urls(
        _source(max_urls=2),
        sessions,
        lambda texts: [],
        httpx.MockTransport(handler),
        verification_limit=1,
    )

    assert summary.status == "FAILED"
    assert summary.stop_reason == "MAX_URLS_EXCEEDED"
    assert summary.planned == 0


@pytest.mark.asyncio
async def test_resume_uses_only_stored_incomplete_urls_without_discovery(monkeypatch, sessions):
    source = _source()
    run_id = create_run(sessions, PROVIDER, {"source": source.name}, concurrency=1)
    urls = (
        "https://registrar.gatech.edu/registration",
        "https://registrar.gatech.edu/registration/holds",
    )
    plan_run(sessions, run_id, urls)
    with sessions() as session, session.begin():
        session.execute(
            update(IngestionRunUnit)
            .where(
                IngestionRunUnit.run_id == run_id,
                IngestionRunUnit.unit_key == urls[0],
            )
            .values(status="SUCCEEDED")
        )

    async def sync_url(source, url, session_factory, embed_fn, transport):
        assert url == urls[1]
        return DocumentSyncResult(source.name, DocumentSyncOutcome.UNCHANGED, 1, fetched=1)

    monkeypatch.setattr("ingestion.documents.sync_source.sync_document_url", sync_url)

    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("resume must not rediscover URLs")

    summary = await sync_document_source_urls(
        source,
        sessions,
        lambda texts: [],
        httpx.MockTransport(forbidden),
        run_id=run_id,
        resume=True,
    )

    assert summary.status == "COMPLETED"
    assert summary.planned_units == urls


def test_document_outcomes_map_to_shared_execution_semantics():
    rate_limited = _unit_result(
        DocumentSyncResult(
            "gt-registrar",
            DocumentSyncOutcome.RATE_LIMITED,
            1,
            reason="HTTP_429",
            retry_after_seconds=30,
        )
    )
    auth = _unit_result(
        DocumentSyncResult(
            "gt-registrar",
            DocumentSyncOutcome.AUTH_REQUIRED,
            1,
            reason="AUTH_REDIRECT",
        )
    )
    parse_failure = _unit_result(
        DocumentSyncResult(
            "gt-registrar",
            DocumentSyncOutcome.EXTRACT_FAILED,
            1,
            fetched=1,
            reason="EXTRACTION_FAILED",
        )
    )

    assert rate_limited.outcome is UnitOutcome.RATE_LIMITED
    assert rate_limited.retry_after_seconds == 30
    assert auth.outcome is UnitOutcome.AUTH_REQUIRED
    assert parse_failure.outcome is UnitOutcome.FAILED
