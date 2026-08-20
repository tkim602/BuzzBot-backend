from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import Chunk, Document, FetchState
from ingestion.chunk import chunk_text
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


@dataclass(frozen=True)
class DocumentSyncResult:
    source: str
    outcome: DocumentSyncOutcome
    requests_used: int
    fetched: int = 0
    changed: int = 0
    chunks_indexed: int = 0
    reason: str | None = None


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
    with session_factory() as session:
        headers = _conditional_headers(session, seed)

    async with httpx.AsyncClient(
        transport=transport,
        timeout=15,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, **headers},
    ) as client:
        response = await client.get(seed)
    requests_used = probe.requests_used + 1

    if response.status_code == 304:
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.UNCHANGED,
            requests_used,
            fetched=1,
        )
    if response.status_code != 200 or not source.allows(str(response.url)):
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.FETCH_FAILED,
            requests_used,
            reason=f"HTTP_{response.status_code}",
        )

    extracted = extract_content(seed, response.text)
    if not extracted.success or not extracted.text:
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.EXTRACT_FAILED,
            requests_used,
            fetched=1,
            reason="EXTRACTION_FAILED",
        )

    fetched = FetchedDocument(
        source_url=seed,
        canonical_url=normalize_url(seed),
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
    existing = session.scalar(
        select(Document).where(Document.canonical_url == fetched.canonical_url)
    )
    if existing is not None and existing.content_hash == digest:
        update_fetch_state(
            session,
            fetched.source_url,
            source_id,
            fetched.etag,
            fetched.last_modified,
            digest,
        )
        chunk_count = session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.doc_id == existing.doc_id)
        )
        return False, int(chunk_count or 0)

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
    chunks = chunk_text(fetched.text, chunk_size=500, chunk_overlap=80, metadata=metadata)
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
