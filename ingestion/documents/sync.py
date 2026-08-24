from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urljoin, urlsplit

import httpx
from lxml import html as lxml_html
from lxml.etree import ParserError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, FetchState
from ingestion.chunk import chunk_text
from ingestion.documents.admission import accepts_path as accepts_admission_path
from ingestion.documents.calendar import (
    CalendarPayloadError,
    calendar_request_headers,
    calendar_request_url,
    parse_calendar_payload,
)
from ingestion.documents.catalog import accepts_path as accepts_catalog_path
from ingestion.documents.discovery import _declared_path_allowed
from ingestion.documents.omscs import accepts_path as accepts_omscs_path
from ingestion.documents.pdf import ExtractedPage, PdfExtractionError, extract_pdf
from ingestion.documents.probe import DocumentProbeStatus, probe_document_source
from ingestion.documents.registrar import accepts_path as accepts_registrar_path
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


class DocumentQualityError(ValueError):
    pass


CHUNKING_VERSION = 3


_REDIRECT_SCOPES = {
    "registrar": accepts_registrar_path,
    "catalog": accepts_catalog_path,
    "omscs": accepts_omscs_path,
    "admissions": accepts_admission_path,
}


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
    content_type: str = "text/html"
    pages: tuple[ExtractedPage, ...] = ()


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
    try:
        with session_factory() as session:
            changed, chunks_indexed = _store_document(session, source, fetched, embed_fn)
            session.commit()
    except DocumentQualityError:
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.EXTRACT_FAILED,
            requests_used,
            fetched=1,
            reason="QUALITY_GATE_FAILED",
        )

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
    source_url = canonical_url
    if not source.allows(canonical_url):
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.FETCH_FAILED,
            0,
            reason="URL_NOT_ALLOWED",
        )

    with session_factory() as session:
        headers = _conditional_headers(session, source_url)
    async with httpx.AsyncClient(
        transport=transport,
        timeout=15,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        response = await client.get(canonical_url, headers=headers)
        requests_used = 1

        if 300 <= response.status_code < 400 and response.status_code != 304:
            location = response.headers.get("Location")
            target_url = urljoin(canonical_url, location or "")
            if "login" in target_url.lower() or "sso" in target_url.lower():
                return DocumentSyncResult(
                    source.name,
                    DocumentSyncOutcome.AUTH_REQUIRED,
                    requests_used,
                    reason="AUTH_REDIRECT",
                )
            same_source_redirect = (
                bool(location)
                and source.allows(target_url)
                and _redirect_in_scope(source, target_url)
            )
            external_allowed_redirect = bool(location) and source.allows_redirect(target_url)
            if not same_source_redirect and not external_allowed_redirect:
                return DocumentSyncResult(
                    source.name,
                    DocumentSyncOutcome.FETCH_FAILED,
                    requests_used,
                    reason="REDIRECT_NOT_ALLOWED",
                )
            fetch_url = target_url
            if same_source_redirect:
                canonical_url = normalize_url(target_url)
                source_url = canonical_url
            with session_factory() as session:
                headers = _conditional_headers(session, source_url)
            response = await client.get(fetch_url, headers=headers)
            requests_used += 1
    if response.status_code == 304:
        with session_factory() as session:
            document = session.scalar(
                select(Document).where(Document.canonical_url == canonical_url)
            )
            if document is not None and (not document.title or not document.content_text):
                return DocumentSyncResult(
                    source.name,
                    DocumentSyncOutcome.EXTRACT_FAILED,
                    requests_used,
                    fetched=1,
                    reason="QUALITY_GATE_FAILED",
                )
            if document is not None and document.content_text:
                stored_chunks = session.scalars(
                    select(Chunk).where(Chunk.doc_id == document.doc_id)
                ).all()
                min_chunk_size = 10 if source.source_type == "academic_calendar" else 50
                if (
                    not stored_chunks
                    or any(chunk.token_count < min_chunk_size for chunk in stored_chunks)
                    or not _uses_current_chunking(document)
                ):
                    fetched = FetchedDocument(
                        source_url=source_url,
                        canonical_url=canonical_url,
                        title=document.title,
                        text=document.content_text,
                        fetched_at=datetime.now(UTC),
                        etag=response.headers.get("ETag") or document.etag,
                        last_modified=(
                            response.headers.get("Last-Modified") or document.last_modified
                        ),
                        edition=_edition(f"{document.title or ''}\n{document.content_text}"),
                    )
                    try:
                        changed, chunks_indexed = _store_document(
                            session, source, fetched, embed_fn
                        )
                        session.commit()
                    except DocumentQualityError:
                        return DocumentSyncResult(
                            source.name,
                            DocumentSyncOutcome.EXTRACT_FAILED,
                            requests_used,
                            fetched=1,
                            reason="QUALITY_GATE_FAILED",
                        )
                    return DocumentSyncResult(
                        source.name,
                        DocumentSyncOutcome.INDEXED,
                        requests_used,
                        fetched=1,
                        changed=int(changed),
                        chunks_indexed=chunks_indexed,
                    )
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
    if response.status_code != 200 or not (
        source.allows(str(response.url)) or source.allows_redirect(str(response.url))
    ):
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.FETCH_FAILED,
            requests_used,
            reason=f"HTTP_{response.status_code}",
        )

    content_type = _response_content_type(response, str(response.url))
    if content_type not in source.content_types:
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.EXTRACT_FAILED,
            requests_used,
            fetched=1,
            reason="UNEXPECTED_CONTENT_TYPE",
        )
    pages: tuple[ExtractedPage, ...] = ()
    if content_type == "application/pdf":
        try:
            pdf = extract_pdf(response.content)
        except PdfExtractionError:
            return DocumentSyncResult(
                source.name,
                DocumentSyncOutcome.EXTRACT_FAILED,
                requests_used,
                fetched=1,
                reason="QUALITY_GATE_FAILED",
            )
        title = pdf.title
        text = "\n\n".join(page.text for page in pdf.pages)
        pages = pdf.pages
    else:
        extracted = extract_content(canonical_url, response.text)
        if not extracted.success or not extracted.text:
            return DocumentSyncResult(
                source.name,
                DocumentSyncOutcome.EXTRACT_FAILED,
                requests_used,
                fetched=1,
                reason="EXTRACTION_FAILED",
            )
        if (
            not extracted.title
            or not extracted.title.strip()
            or _is_recognized_error_page(response.text, extracted.title)
        ):
            return DocumentSyncResult(
                source.name,
                DocumentSyncOutcome.EXTRACT_FAILED,
                requests_used,
                fetched=1,
                reason="QUALITY_GATE_FAILED",
            )
        title = extracted.title
        text = extracted.text
    fetched = FetchedDocument(
        source_url=source_url,
        canonical_url=canonical_url,
        title=title,
        text=text,
        fetched_at=datetime.now(UTC),
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
        edition=_edition(f"{title}\n{text}"),
        content_type=content_type,
        pages=pages,
    )
    try:
        with session_factory() as session:
            changed, chunks_indexed = _store_document(session, source, fetched, embed_fn)
            session.commit()
    except DocumentQualityError:
        return DocumentSyncResult(
            source.name,
            DocumentSyncOutcome.EXTRACT_FAILED,
            requests_used,
            fetched=1,
            reason="QUALITY_GATE_FAILED",
        )
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


def _redirect_in_scope(source: DocumentSource, url: str) -> bool:
    path = urlsplit(normalize_url(url)).path
    if source.adapter == "paths":
        return _declared_path_allowed(source, path)
    accepts_path = _REDIRECT_SCOPES.get(source.adapter or source.authority)
    return accepts_path is not None and accepts_path(path)


def _uses_current_chunking(document: Document) -> bool:
    metadata = document.metadata_json if isinstance(document.metadata_json, dict) else {}
    version = metadata.get("chunking_version")
    return isinstance(version, int) and bool(version == CHUNKING_VERSION)


def _store_document(
    session: Session,
    source: DocumentSource,
    fetched: FetchedDocument,
    embed_fn,
) -> tuple[bool, int]:
    if not fetched.title or not fetched.title.strip() or not fetched.text.strip():
        raise DocumentQualityError("document title and body are required")
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
        "adapter": source.adapter or source.authority,
        "vertical": source.vertical,
        "content_type": fetched.content_type,
        "freshness_class": source.freshness_class,
        "fetched_at": fetched.fetched_at.isoformat(),
        "chunking_version": CHUNKING_VERSION,
    }
    if fetched.edition:
        metadata["edition"] = fetched.edition
    if fetched.pages:
        metadata["page_count"] = len(fetched.pages)
    min_chunk_size = (
        10
        if source.source_type == "academic_calendar" or fetched.content_type == "application/pdf"
        else 50
    )
    existing = session.scalar(
        select(Document).where(Document.canonical_url == fetched.canonical_url)
    )
    if existing is not None and existing.content_hash == digest:
        uses_current_chunking = _uses_current_chunking(existing)
        existing.source_id = source_id
        existing.fetched_at = fetched.fetched_at
        existing.etag = fetched.etag
        existing.last_modified = fetched.last_modified
        existing.metadata_json = metadata
        stored_chunks = session.scalars(select(Chunk).where(Chunk.doc_id == existing.doc_id)).all()
        if (
            uses_current_chunking
            and stored_chunks
            and all(chunk.token_count >= min_chunk_size for chunk in stored_chunks)
        ):
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

    chunks = _chunks_for_fetched(fetched, metadata, min_chunk_size)
    if not chunks:
        raise DocumentQualityError("document produced no chunks")
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
    stored_document = existing or session.get(Document, document_id)
    if stored_document is not None:
        stored_document.metadata_json = metadata
    indexed = index_chunks(
        session,
        document_id,
        source_id,
        chunks,
        fetched.canonical_url,
        fetched.title,
        None
        if fetched.content_type == "application/pdf"
        else "\n".join(extract_headings(fetched.text)) or None,
        fetched.fetched_at,
        embed_fn,
    )
    if indexed != len(chunks):
        raise DocumentQualityError("document chunks were not fully indexed")
    update_fetch_state(
        session,
        fetched.source_url,
        source_id,
        fetched.etag,
        fetched.last_modified,
        digest,
    )
    return True, indexed


def _chunks_for_fetched(
    fetched: FetchedDocument,
    metadata: dict[str, object],
    min_chunk_size: int,
):
    if not fetched.pages:
        return chunk_text(
            fetched.text,
            chunk_size=500,
            chunk_overlap=80,
            min_chunk_size=min_chunk_size,
            metadata=metadata,
        )
    chunks = []
    for page in fetched.pages:
        chunks.extend(
            chunk_text(
                page.text,
                chunk_size=500,
                chunk_overlap=80,
                min_chunk_size=min_chunk_size,
                metadata={
                    **metadata,
                    "content_type": "application/pdf",
                    "page_start": page.page_number,
                    "page_end": page.page_number,
                },
            )
        )
    return chunks


def _response_content_type(response: httpx.Response, url: str) -> str:
    declared = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
    if declared == "text/plain" and response.content.lstrip().startswith(b"<"):
        return "text/html"
    if declared:
        return declared
    return "application/pdf" if urlsplit(url).path.lower().endswith(".pdf") else "text/html"


def _edition(text: str) -> str | None:
    match = re.search(r"\b(20\d{2}-20\d{2})\b", text)
    return match.group(1) if match else None


def _retry_after_seconds(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def _is_recognized_error_page(body: str, title: str) -> bool:
    normalized_title = " ".join(title.lower().split())
    error_titles = (
        "access denied",
        "forbidden",
        "page not found",
        "just a moment",
        "request rejected",
        "attention required",
        "service unavailable",
        "temporarily unavailable",
        "internal server error",
        "sign in",
        "log in",
        "login",
    )
    if normalized_title == "error" or any(item in normalized_title for item in error_titles):
        return True
    try:
        root = lxml_html.fromstring(body)
    except (ParserError, TypeError, ValueError):
        return False
    return bool(root.xpath("//input[translate(@type, 'PASSWORD', 'password')='password']"))
