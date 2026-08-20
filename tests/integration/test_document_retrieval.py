from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.retrieval import documents as document_retrieval
from app.retrieval.documents import PolicyQuery, search_policy_docs
from db.models import FetchState, Source
from db.session import sync_engine
from ingestion.documents.registry import DocumentSource
from ingestion.documents.sync import FetchedDocument, _store_document

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="set RUN_DB_TESTS=1 for PostgreSQL tests"
)


@pytest.mark.asyncio
async def test_hybrid_document_retrieval_returns_official_citation(monkeypatch):
    suffix = uuid.uuid4().hex
    source_name = f"test-calendar-{suffix}"
    source = DocumentSource(
        source_name,
        "academic_calendar",
        "academic_calendar",
        ("https://registrar.gatech.edu/",),
        (f"https://registrar.gatech.edu/test-calendar-{suffix}",),
        1,
    )
    canonical_url = source.seed_urls[0]
    text = "Fall 2026 registration deadline is August 21. " * 40
    vector = [1.0, *([0.0] * 1535)]
    with Session(sync_engine) as session:
        changed, indexed = _store_document(
            session,
            source,
            FetchedDocument(
                canonical_url,
                canonical_url,
                "Current Academic Calendar",
                text,
                datetime.now(UTC),
                None,
                None,
                "2026-2027",
            ),
            lambda texts: [vector for _ in texts],
        )
        session.commit()
        assert changed is True and indexed > 0

    monkeypatch.setitem(
        document_retrieval.SOURCE_NAMES_BY_TYPE,
        "academic_calendar",
        source_name,
    )
    monkeypatch.setattr(settings, "rag_enable_reranking", False)
    async_engine = create_async_engine(settings.database_url)
    try:
        sessions = async_sessionmaker(async_engine, expire_on_commit=False)
        async with sessions() as session:
            evidence = await search_policy_docs(
                session,
                PolicyQuery("What is the exact Fall 2026 registration deadline?"),
                vector,
            )
        assert evidence
        assert evidence[0].canonical_url == canonical_url
        assert evidence[0].source_type == "academic_calendar"
        assert evidence[0].authority == "academic_calendar"
        assert evidence[0].edition == "2026-2027"
        assert evidence[0].retrieval_method in {"vector", "fts", "hybrid_rrf"}
    finally:
        await async_engine.dispose()
        with Session(sync_engine) as session:
            stored_source = session.scalar(select(Source).where(Source.name == source_name))
            if stored_source is not None:
                session.execute(delete(FetchState).where(FetchState.source_id == stored_source.id))
                for document in list(stored_source.documents):
                    session.delete(document)
                session.flush()
                session.delete(stored_source)
                session.commit()
