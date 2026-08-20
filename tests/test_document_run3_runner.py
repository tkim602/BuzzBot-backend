from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import IngestionRun, IngestionRunUnit
from ingestion.documents.registry import DocumentSource
from ingestion.documents.sync import DocumentSyncOutcome, DocumentSyncResult


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


def _source(name: str, vertical: str) -> DocumentSource:
    return DocumentSource(
        name,
        "policy",
        name,
        (f"https://{name}.gatech.edu/",),
        (f"https://{name}.gatech.edu/policies",),
        5,
        vertical=vertical,
        adapter="paths",
        allowed_path_prefixes=("/policies",),
        profiles=("run3",),
    )


@pytest.mark.asyncio
async def test_fresh_profile_run_freezes_heterogeneous_manifest(monkeypatch, sessions):
    from ingestion.documents.sync_all import profile_coverage, sync_document_profile

    sources = (_source("gt-one", "finance"), _source("gt-two", "career"))

    async def discover(source, transport, verification_limit):
        assert verification_limit is None
        return (source.seed_urls[0], f"{source.seed_urls[0]}/details"), None

    called: list[tuple[str, str]] = []

    async def sync_url(source, url, session_factory, embed_fn, transport):
        called.append((source.name, url))
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.INDEXED,
            1,
            fetched=1,
            changed=1,
            chunks_indexed=2,
        )

    monkeypatch.setattr("ingestion.documents.sync_all._discover", discover)
    monkeypatch.setattr("ingestion.documents.sync_all.sync_document_url", sync_url)

    summary = await sync_document_profile(
        "run3", sources, sessions, lambda texts: [], concurrency=1
    )

    assert summary.status == "COMPLETED"
    assert summary.planned_units == (
        "gt-one:0000",
        "gt-one:0001",
        "gt-two:0000",
        "gt-two:0001",
    )
    assert called == [
        ("gt-one", "https://gt-one.gatech.edu/policies"),
        ("gt-one", "https://gt-one.gatech.edu/policies/details"),
        ("gt-two", "https://gt-two.gatech.edu/policies"),
        ("gt-two", "https://gt-two.gatech.edu/policies/details"),
    ]
    assert summary.scope["manifest"][2] == {
        "unit_key": "gt-two:0000",
        "source": "gt-two",
        "url": "https://gt-two.gatech.edu/policies",
        "adapter": "paths",
        "vertical": "career",
    }
    assert profile_coverage(sessions, summary.run_id) == {
        "career": {"planned": 2, "succeeded": 2, "failed": 0, "remaining": 0},
        "finance": {"planned": 2, "succeeded": 2, "failed": 0, "remaining": 0},
    }


@pytest.mark.asyncio
async def test_profile_resume_uses_stored_manifest_without_discovery(monkeypatch, sessions):
    from ingestion.documents.sync_all import sync_document_profile

    source = _source("gt-one", "finance")

    async def discover(source, transport, verification_limit):
        return (source.seed_urls[0],), None

    async def first_sync(*args):
        return DocumentSyncResult("gt-one", DocumentSyncOutcome.EXTRACT_FAILED, 1)

    monkeypatch.setattr("ingestion.documents.sync_all._discover", discover)
    monkeypatch.setattr("ingestion.documents.sync_all.sync_document_url", first_sync)
    first = await sync_document_profile("run3", (source,), sessions, lambda texts: [])
    assert first.status == "PARTIAL"

    async def forbidden_discovery(*args):
        raise AssertionError("resume must not rediscover")

    async def successful_sync(*args):
        return DocumentSyncResult("gt-one", DocumentSyncOutcome.UNCHANGED, 1, fetched=1)

    monkeypatch.setattr("ingestion.documents.sync_all._discover", forbidden_discovery)
    monkeypatch.setattr("ingestion.documents.sync_all.sync_document_url", successful_sync)

    resumed = await sync_document_profile(
        "run3",
        (source,),
        sessions,
        lambda texts: [],
        run_id=first.run_id,
        resume=True,
    )

    assert resumed.status == "COMPLETED"


@pytest.mark.asyncio
async def test_profile_planning_failure_executes_no_units(monkeypatch, sessions):
    from ingestion.documents.sync_all import profile_coverage, sync_document_profile

    sources = (_source("gt-one", "finance"), _source("gt-two", "career"))
    calls = 0

    async def discover(source, transport, verification_limit):
        if source.name == "gt-two":
            return (), "MAX_URLS_EXCEEDED"
        return (source.seed_urls[0],), None

    async def forbidden_sync(*args):
        nonlocal calls
        calls += 1
        raise AssertionError("planning failure must execute nothing")

    monkeypatch.setattr("ingestion.documents.sync_all._discover", discover)
    monkeypatch.setattr("ingestion.documents.sync_all.sync_document_url", forbidden_sync)

    summary = await sync_document_profile("run3", sources, sessions, lambda texts: [])

    assert summary.status == "FAILED"
    assert summary.stop_reason == "gt-two:MAX_URLS_EXCEEDED"
    assert summary.planned == 0
    assert calls == 0
    assert profile_coverage(sessions, summary.run_id) == {}


@pytest.mark.asyncio
async def test_verification_limit_is_global_and_applied_after_discovery(monkeypatch, sessions):
    from ingestion.documents.sync_all import sync_document_profile

    sources = (_source("gt-one", "finance"), _source("gt-two", "career"))

    async def discover(source, transport, verification_limit):
        assert verification_limit is None
        return (source.seed_urls[0], f"{source.seed_urls[0]}/details"), None

    monkeypatch.setattr("ingestion.documents.sync_all._discover", discover)
    monkeypatch.setattr(
        "ingestion.documents.sync_all.sync_document_url",
        lambda *args: (_ for _ in ()).throw(AssertionError("async replacement required")),
    )

    async def sync_url(source, url, session_factory, embed_fn, transport):
        return DocumentSyncResult(source.name, DocumentSyncOutcome.UNCHANGED, 1, fetched=1)

    monkeypatch.setattr("ingestion.documents.sync_all.sync_document_url", sync_url)
    summary = await sync_document_profile(
        "run3", sources, sessions, lambda texts: [], verification_limit=2
    )

    assert summary.planned == 2
    assert [item["source"] for item in summary.scope["manifest"]] == ["gt-one", "gt-one"]
