from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.models import Chunk, Document, FetchState, Source
from db.session import sync_engine
from ingestion.chunk import chunk_text
from ingestion.documents.registry import DocumentSource
from ingestion.documents.sync import (
    DocumentSyncOutcome,
    FetchedDocument,
    _store_document,
    sync_document_source,
    sync_document_url,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="set RUN_DB_TESTS=1 for PostgreSQL tests"
)


def test_chunks_store_and_embed_only_their_local_section_context():
    suffix = uuid.uuid4().hex
    url = f"https://registrar.gatech.edu/registration/context-{suffix}"
    source = DocumentSource(
        f"test-context-{suffix}",
        "official_policy",
        "registrar",
        ("https://registrar.gatech.edu/registration",),
        (url,),
        1,
    )
    recommendations = "Recommendations are optional. " * 70
    deadlines = "Application deadlines are published. " * 70
    fetched = FetchedDocument(
        url,
        url,
        "Admissions",
        f"## Recommendations\n{recommendations}\n## Deadlines\n{deadlines}",
        datetime.now(UTC),
        None,
        None,
        None,
    )
    embedded: list[str] = []

    def embed(texts: list[str]) -> list[list[float]]:
        embedded.extend(texts)
        return [[0.0] * 1536 for _ in texts]

    try:
        with Session(sync_engine) as session:
            _store_document(session, source, fetched, embed)
            session.commit()
        with Session(sync_engine) as session:
            document = session.scalar(select(Document).where(Document.canonical_url == url))
            assert document is not None
            chunks = list(document.chunks)
            expected_raw = {
                chunk.text for chunk in chunk_text(fetched.text, metadata=document.metadata_json)
            }
            assert {chunk.headings for chunk in chunks} == {"Recommendations", "Deadlines"}
            assert {chunk.chunk_text for chunk in chunks} == expected_raw
            assert embedded[0].startswith(f"Admissions\n{chunks[0].headings}\n")
            assert chunks[0].chunk_text in embedded[0]
    finally:
        with Session(sync_engine) as session:
            document = session.scalar(select(Document).where(Document.canonical_url == url))
            if document is not None:
                session.delete(document)
            stored_source = session.scalar(select(Source).where(Source.name == source.name))
            if stored_source is not None:
                session.delete(stored_source)
            session.commit()


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


@pytest.mark.asyncio
async def test_failed_embedding_rolls_back_one_url_replacement():
    suffix = uuid.uuid4().hex
    url = f"https://registrar.gatech.edu/registration/test-atomic-{suffix}"
    source = DocumentSource(
        f"test-atomic-{suffix}",
        "official_policy",
        "registrar",
        ("https://registrar.gatech.edu/registration",),
        (url,),
        1,
    )

    @contextmanager
    def sessions():
        with Session(sync_engine) as session:
            yield session

    def response(text: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=f"<html><title>Atomic</title><body>{text * 100}</body></html>",
                request=request,
            )

        return httpx.MockTransport(handler)

    def embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]

    try:
        await sync_document_url(source, url, sessions, embed, response("trusted policy "))
        with Session(sync_engine) as session:
            original = session.scalar(select(Document).where(Document.canonical_url == url))
            assert original is not None
            original_text = original.content_text
            original_chunks = tuple(chunk.chunk_text for chunk in original.chunks)

        def fail_embedding(_texts: list[str]) -> list[list[float]]:
            raise RuntimeError("embedding failed")

        with pytest.raises(RuntimeError, match="embedding failed"):
            await sync_document_url(
                source,
                url,
                sessions,
                fail_embedding,
                response("untrusted replacement "),
            )

        with Session(sync_engine) as session:
            preserved = session.scalar(select(Document).where(Document.canonical_url == url))
            assert preserved is not None
            assert preserved.content_text == original_text
            assert tuple(chunk.chunk_text for chunk in preserved.chunks) == original_chunks
    finally:
        with Session(sync_engine) as session:
            document = session.scalar(select(Document).where(Document.canonical_url == url))
            if document is not None:
                session.delete(document)
            session.execute(delete(FetchState).where(FetchState.url == url))
            stored_source = session.scalar(select(Source).where(Source.name == source.name))
            if stored_source is not None:
                session.delete(stored_source)
            session.commit()


@pytest.mark.asyncio
async def test_zero_chunks_fails_quality_gate_and_preserves_trusted_document(monkeypatch):
    suffix = uuid.uuid4().hex
    url = f"https://registrar.gatech.edu/registration/test-zero-chunks-{suffix}"
    source = DocumentSource(
        f"test-zero-chunks-{suffix}",
        "official_policy",
        "registrar",
        ("https://registrar.gatech.edu/registration",),
        (url,),
        1,
    )

    @contextmanager
    def sessions():
        with Session(sync_engine) as session:
            yield session

    current_body = "trusted registration policy " * 100
    replacement_body = "replacement registration policy " * 100

    def response(body: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=f"<html><title>Registration Policy</title><body>{body}</body></html>",
                request=request,
            )

        return httpx.MockTransport(handler)

    def embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]

    try:
        first = await sync_document_url(source, url, sessions, embed, response(current_body))
        assert first.outcome is DocumentSyncOutcome.INDEXED
        with Session(sync_engine) as session:
            original = session.scalar(select(Document).where(Document.canonical_url == url))
            assert original is not None
            original_text = original.content_text
            original_chunks = tuple(chunk.chunk_text for chunk in original.chunks)

        monkeypatch.setattr("ingestion.documents.sync.chunk_text", lambda *args, **kwargs: [])
        result = await sync_document_url(source, url, sessions, embed, response(replacement_body))

        assert result.outcome is DocumentSyncOutcome.EXTRACT_FAILED
        assert result.reason == "QUALITY_GATE_FAILED"
        with Session(sync_engine) as session:
            preserved = session.scalar(select(Document).where(Document.canonical_url == url))
            assert preserved is not None
            assert preserved.content_text == original_text
            assert tuple(chunk.chunk_text for chunk in preserved.chunks) == original_chunks
    finally:
        with Session(sync_engine) as session:
            document = session.scalar(select(Document).where(Document.canonical_url == url))
            if document is not None:
                session.delete(document)
            session.execute(delete(FetchState).where(FetchState.url == url))
            stored_source = session.scalar(select(Source).where(Source.name == source.name))
            if stored_source is not None:
                session.delete(stored_source)
            session.commit()


@pytest.mark.asyncio
async def test_safe_alias_redirect_reuses_existing_canonical_document():
    suffix = uuid.uuid4().hex
    alias = f"https://registrar.gatech.edu/registration/alias-{suffix}"
    target = f"https://registrar.gatech.edu/registration/target-{suffix}"
    source = DocumentSource(
        f"test-alias-{suffix}",
        "official_policy",
        "registrar",
        ("https://registrar.gatech.edu/registration",),
        (target,),
        2,
    )
    html = (
        "<html><title>Registration Assistance</title><body>"
        + "official registration assistance " * 100
        + "</body></html>"
    )

    @contextmanager
    def sessions():
        with Session(sync_engine) as session:
            yield session

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == alias:
            return httpx.Response(301, headers={"Location": target}, request=request)
        return httpx.Response(200, text=html, request=request)

    embedding_calls = 0

    def embed(texts: list[str]) -> list[list[float]]:
        nonlocal embedding_calls
        embedding_calls += 1
        return [[0.0] * 1536 for _ in texts]

    try:
        first = await sync_document_url(
            source, target, sessions, embed, httpx.MockTransport(handler)
        )
        first_embedding_calls = embedding_calls
        alias_result = await sync_document_url(
            source, alias, sessions, embed, httpx.MockTransport(handler)
        )

        assert first.outcome is DocumentSyncOutcome.INDEXED
        assert alias_result.outcome is DocumentSyncOutcome.UNCHANGED
        assert embedding_calls == first_embedding_calls
        with Session(sync_engine) as session:
            documents = session.scalars(
                select(Document).join(Source).where(Source.name == source.name)
            ).all()
            assert [document.canonical_url for document in documents] == [target]
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
