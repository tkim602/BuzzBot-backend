from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.models import Chunk, Document, FetchState, Source
from db.session import sync_engine
from ingestion.documents.registry import DocumentSource
from ingestion.documents.sync import DocumentSyncOutcome, sync_document_source

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="set RUN_DB_TESTS=1 for PostgreSQL tests"
)


@pytest.mark.asyncio
async def test_identical_document_skips_reembedding_and_keeps_chunks():
    suffix = uuid.uuid4().hex
    source = DocumentSource(
        f"test-doc-{suffix}",
        "official_policy",
        "registrar",
        ("https://registrar.gatech.edu/",),
        (f"https://registrar.gatech.edu/test-{suffix}",),
        1,
    )
    html = (
        "<html><title>Registration</title><body>"
        + "official registration policy " * 80
        + "</body></html>"
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, text=html, headers={"Content-Type": "text/html"}, request=request
        )

    embedding_calls = 0

    def embed(texts: list[str]) -> list[list[float]]:
        nonlocal embedding_calls
        embedding_calls += 1
        return [[0.0] * 1536 for _ in texts]

    @contextmanager
    def sessions():
        with Session(sync_engine) as session:
            yield session

    try:
        first = await sync_document_source(source, sessions, embed, httpx.MockTransport(handler))
        first_embedding_calls = embedding_calls
        second = await sync_document_source(source, sessions, embed, httpx.MockTransport(handler))

        assert first.outcome is DocumentSyncOutcome.INDEXED
        assert second.outcome is DocumentSyncOutcome.UNCHANGED
        assert first_embedding_calls > 0
        assert embedding_calls == first_embedding_calls
        with Session(sync_engine) as session:
            document = session.scalar(
                select(Document).join(Source).where(Source.name == source.name)
            )
            assert document is not None
            assert len(document.chunks) == first.chunks_indexed
    finally:
        with Session(sync_engine) as session:
            stored_source = session.scalar(select(Source).where(Source.name == source.name))
            if stored_source is not None:
                session.execute(delete(FetchState).where(FetchState.source_id == stored_source.id))
                for document in list(stored_source.documents):
                    session.delete(document)
                session.flush()
                session.delete(stored_source)
                session.commit()


def test_same_content_reclassifies_authority_without_reembedding():
    suffix = uuid.uuid4().hex
    url = f"https://registrar.gatech.edu/test-reclassify-{suffix}"
    first_source = DocumentSource(
        f"test-policy-{suffix}",
        "official_policy",
        "registrar",
        ("https://registrar.gatech.edu/",),
        (url,),
        1,
    )
    calendar_source = DocumentSource(
        f"test-calendar-{suffix}",
        "academic_calendar",
        "academic_calendar",
        ("https://registrar.gatech.edu/",),
        (url,),
        1,
    )
    from datetime import UTC, datetime

    from ingestion.documents.sync import FetchedDocument, _store_document

    fetched = FetchedDocument(
        url,
        url,
        "Current Academic Calendar",
        "Official calendar registration deadline. " * 80,
        datetime.now(UTC),
        None,
        None,
        "2026-2027",
    )
    embedding_calls = 0

    def embed(texts: list[str]) -> list[list[float]]:
        nonlocal embedding_calls
        embedding_calls += 1
        return [[0.0] * 1536 for _ in texts]

    try:
        with Session(sync_engine) as session:
            _store_document(session, first_source, fetched, embed)
            session.commit()
        first_calls = embedding_calls
        with Session(sync_engine) as session:
            changed, _ = _store_document(session, calendar_source, fetched, embed)
            session.commit()

        assert changed is False
        assert embedding_calls == first_calls
        with Session(sync_engine) as session:
            document = session.scalar(select(Document).where(Document.canonical_url == url))
            assert document is not None
            assert document.source.name == calendar_source.name
            chunks = session.scalars(select(Chunk).where(Chunk.doc_id == document.doc_id)).all()
            assert {chunk.metadata_json["source_type"] for chunk in chunks} == {"academic_calendar"}
            assert {chunk.metadata_json["authority"] for chunk in chunks} == {"academic_calendar"}
    finally:
        with Session(sync_engine) as session:
            document = session.scalar(select(Document).where(Document.canonical_url == url))
            if document is not None:
                session.delete(document)
            session.execute(delete(FetchState).where(FetchState.url == url))
            session.flush()
            for name in (first_source.name, calendar_source.name):
                stored_source = session.scalar(select(Source).where(Source.name == name))
                if stored_source is not None:
                    session.delete(stored_source)
            session.commit()
