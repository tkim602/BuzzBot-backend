from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Chunk, Document, FetchState
from ingestion.chunk import chunk_text
from ingestion.documents.calendar import (
    CalendarPayloadError,
    calendar_request_headers,
    calendar_request_url,
    parse_calendar_payload,
)
from ingestion.documents.probe import DocumentProbeStatus, probe_document_source
from ingestion.documents.registry import DocumentSource
from ingestion.extract import extract_content
from ingestion.index import index_chunks, update_fetch_state, upsert_document, upsert_source
from ingestion.normalize import content_hash, extract_headings, normalize_url
from ingestion.probes.cli import USER_AGENT


class DocumentSyncOutcome(StrEnum):
    INDEXED = "INDEXED"
    UNCHANGED = "UNCHANGED"
    PROBE_FAILED = "PROBE_FAILED"
    FETCH_FAILED = "FETCH_FAILED"
    EXTRACT_FAILED = "EXTRACT_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"


@dataclass(frozen=True)
class DocumentSyncResult:
    source: str
    outcome: DocumentSyncOutcome
    requests_used: int
    fetched: int = 0
    changed: int = 0
    chunks_indexed: int = 0
    reason: str | None = None
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class FetchedDocument:
    source_url: str
    canonical_url: str
    title: str | None
    text: str
    fetched_at: datetime
    etag: str | None
    last_modified: str | None
    edition: str | None


async def sync_document_source(
    source: DocumentSource,
    session_factory: Callable[[], AbstractContextManager[Session]],
    embed_fn,
    transport: httpx.AsyncBaseTransport | None = None,
    max_documents: int = 1,
) -> DocumentSyncResult:
    if max_documents != 1:
        raise ValueError("the initial document sync supports exactly one document")

    probe = await probe_document_source(source, transport)
    if probe.status is not DocumentProbeStatus.READY:
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.PROBE_FAILED,
            probe.requests_used,
            reason=probe.reason or probe.status.value,
        )

    seed = source.seed_urls[0]
    if source.source_type != "academic_calendar":
        result = await sync_document_url(
            source,
            seed,
            session_factory,
            embed_fn,
            transport,
        )
        return replace(result, requests_used=probe.requests_used + result.requests_used)

    if source.source_type == "academic_calendar":
        if probe.edition is None:
            return DocumentSyncResult(
                source.name,
                DocumentSyncOutcome.EXTRACT_FAILED,
                probe.requests_used,
                reason="CALENDAR_YEAR_NOT_FOUND",
            )
        data_url = calendar_request_url(seed, probe.edition)
        async with httpx.AsyncClient(
            transport=transport,
            timeout=15,
            follow_redirects=False,
            headers=calendar_request_headers(seed),
        ) as client:
            response = await client.get(data_url)
        requests_used = probe.requests_used + 1
        if response.status_code != 200 or not source.allows(str(response.url)):
            return DocumentSyncResult(
                source.name,
                DocumentSyncOutcome.FETCH_FAILED,
                requests_used,
                reason=f"HTTP_{response.status_code}",
            )
        try:
            calendar = parse_calendar_payload(probe.edition, response.json())
        except (CalendarPayloadError, ValueError) as exc:
            return DocumentSyncResult(
                source.name,
                DocumentSyncOutcome.EXTRACT_FAILED,
                requests_used,
                fetched=1,
                reason=str(exc) or "INVALID_JSON",
            )
        fetched = FetchedDocument(
            source_url=data_url,
            canonical_url=normalize_url(seed),
            title=calendar.title,
            text=calendar.text,
            fetched_at=datetime.now(UTC),
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            edition=calendar.edition,
        )
    with session_factory() as session:
        changed, chunks_indexed = _store_document(session, source, fetched, embed_fn)
        session.commit()

    return DocumentSyncResult(
        source.name,
        DocumentSyncOutcome.INDEXED if changed else DocumentSyncOutcome.UNCHANGED,
        requests_used,
        fetched=1,
        changed=int(changed),
        chunks_indexed=chunks_indexed,
    )


async def sync_document_url(
    source: DocumentSource,
    url: str,
    session_factory: Callable[[], AbstractContextManager[Session]],
    embed_fn,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DocumentSyncResult:
    canonical_url = normalize_url(url)
    if not source.allows(canonical_url):
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.FETCH_FAILED,
            0,
            reason="URL_NOT_ALLOWED",
        )

    with session_factory() as session:
        headers = _conditional_headers(session, canonical_url)
    async with httpx.AsyncClient(
        transport=transport,
        timeout=15,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, **headers},
    ) as client:
        response = await client.get(canonical_url)
    requests_used = 1

    if 300 <= response.status_code < 400:
        target = urljoin(canonical_url, response.headers.get("Location", ""))
        if "login" in target.lower() or "sso" in target.lower():
            return DocumentSyncResult(
                source.name,
                DocumentSyncOutcome.AUTH_REQUIRED,
                requests_used,
                reason="AUTH_REDIRECT",
            )
        if source.allows(target) and normalize_url(target) == canonical_url:
            async with httpx.AsyncClient(
                transport=transport,
                timeout=15,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT, **headers},
            ) as client:
                response = await client.get(target)
            requests_used += 1
    if response.status_code == 304:
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.UNCHANGED,
            requests_used,
            fetched=1,
        )
    if response.status_code == 429:
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.RATE_LIMITED,
            requests_used,
            reason="HTTP_429",
            retry_after_seconds=_retry_after_seconds(response.headers.get("Retry-After")),
        )
    if response.status_code != 200 or not source.allows(str(response.url)):
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.FETCH_FAILED,
            requests_used,
            reason=f"HTTP_{response.status_code}",
        )

    extracted = extract_content(canonical_url, response.text)
    if not extracted.success or not extracted.text:
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.EXTRACT_FAILED,
            requests_used,
            fetched=1,
            reason="EXTRACTION_FAILED",
        )
    fetched = FetchedDocument(
        source_url=canonical_url,
        canonical_url=canonical_url,
        title=extracted.title,
        text=extracted.text,
        fetched_at=datetime.now(UTC),
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
        edition=_edition(f"{extracted.title or ''}\n{extracted.text}"),
    )
    with session_factory() as session:
        changed, chunks_indexed = _store_document(session, source, fetched, embed_fn)
        session.commit()
    return DocumentSyncResult(
        source.name,
        DocumentSyncOutcome.INDEXED if changed else DocumentSyncOutcome.UNCHANGED,
        requests_used,
        fetched=1,
        changed=int(changed),
        chunks_indexed=chunks_indexed,
    )


def _conditional_headers(session: Session, url: str) -> dict[str, str]:
    state = session.scalar(select(FetchState).where(FetchState.url == url))
    headers: dict[str, str] = {}
    if state and state.etag:
        headers["If-None-Match"] = state.etag
    if state and state.last_modified:
        headers["If-Modified-Since"] = state.last_modified
    return headers


def _store_document(
    session: Session,
    source: DocumentSource,
    fetched: FetchedDocument,
    embed_fn,
) -> tuple[bool, int]:
    source_id = upsert_source(
        session,
        source.name,
        source.allowed_roots[0],
        True,
        f"Official Georgia Tech authority: {source.authority}",
    )
    digest = content_hash(fetched.text)
    metadata = {
        "canonical_url": fetched.canonical_url,
        "title": fetched.title,
        "source": source.name,
        "source_type": source.source_type,
        "authority": source.authority,
        "fetched_at": fetched.fetched_at.isoformat(),
    }
    if fetched.edition:
        metadata["edition"] = fetched.edition
    min_chunk_size = 10 if source.source_type == "academic_calendar" else 50
    existing = session.scalar(
        select(Document).where(Document.canonical_url == fetched.canonical_url)
    )
    if existing is not None and existing.content_hash == digest:
        existing.source_id = source_id
        existing.fetched_at = fetched.fetched_at
        existing.etag = fetched.etag
        existing.last_modified = fetched.last_modified
        existing.metadata_json = metadata
        stored_chunks = session.scalars(select(Chunk).where(Chunk.doc_id == existing.doc_id)).all()
        if stored_chunks and all(chunk.token_count >= min_chunk_size for chunk in stored_chunks):
            for chunk in stored_chunks:
                chunk.source_id = source_id
                chunk.url = fetched.canonical_url
                chunk.title = fetched.title
                chunk.fetched_at = fetched.fetched_at
                chunk.metadata_json = {**(chunk.metadata_json or {}), **metadata}
            update_fetch_state(
                session,
                fetched.source_url,
                source_id,
                fetched.etag,
                fetched.last_modified,
                digest,
            )
            return False, len(stored_chunks)

    document_id = upsert_document(
        session,
        source_id,
        fetched.canonical_url,
        fetched.title,
        fetched.text,
        digest,
        fetched.etag,
        fetched.last_modified,
    )
    chunks = chunk_text(
        fetched.text,
        chunk_size=500,
        chunk_overlap=80,
        min_chunk_size=min_chunk_size,
        metadata=metadata,
    )
    indexed = index_chunks(
        session,
        document_id,
        source_id,
        chunks,
        fetched.canonical_url,
        fetched.title,
        "\n".join(extract_headings(fetched.text)) or None,
        fetched.fetched_at,
        embed_fn,
    )
    update_fetch_state(
        session,
        fetched.source_url,
        source_id,
        fetched.etag,
        fetched.last_modified,
        digest,
    )
    return True, indexed


def _edition(text: str) -> str | None:
    match = re.search(r"\b(20\d{2}-20\d{2})\b", text)
    return match.group(1) if match else None


def _retry_after_seconds(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None
