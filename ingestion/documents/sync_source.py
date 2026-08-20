from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from urllib.parse import urljoin

import httpx
from lxml.etree import ParserError
from sqlalchemy.orm import Session

from ingestion.documents.catalog import discover_urls as discover_catalog_urls
from ingestion.documents.discovery import MaxUrlsExceededError
from ingestion.documents.registrar import discover_urls as discover_registrar_urls
from ingestion.documents.registry import DocumentSource
from ingestion.documents.sync import (
    DocumentSyncOutcome,
    DocumentSyncResult,
    sync_document_url,
)
from ingestion.orchestration import (
    RunSummary,
    UnitOutcome,
    UnitResult,
    create_run,
    fail_run,
    load_run_summary,
    plan_run,
    run_batch,
)
from ingestion.probes.cli import USER_AGENT

PROVIDER = "official-documents"
SessionFactory = Callable[[], AbstractContextManager[Session]]


async def sync_document_source_urls(
    source: DocumentSource,
    session_factory: SessionFactory,
    embed_fn,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    run_id: uuid.UUID | None = None,
    resume: bool = False,
    verification_limit: int | None = None,
    concurrency: int = 2,
    retry_limit: int = 2,
) -> RunSummary:
    if resume:
        if run_id is None or verification_limit is not None:
            raise ValueError("resume requires run_id and no discovery limit")
        summary = load_run_summary(session_factory, run_id)
        if summary.provider != PROVIDER or summary.scope.get("source") != source.name:
            raise ValueError("run does not belong to this document source")
    else:
        if run_id is not None or verification_limit is not None and verification_limit < 1:
            raise ValueError("a fresh run requires no run_id and a positive verification limit")
        scope: dict[str, object] = {"source": source.name}
        if verification_limit is not None:
            scope["verification_limit"] = verification_limit
        run_id = create_run(
            session_factory,
            PROVIDER,
            scope,
            concurrency=concurrency,
            retry_limit=retry_limit,
        )
        urls, error = await _discover(source, transport, verification_limit)
        if error is not None:
            return fail_run(session_factory, run_id, error)
        plan_run(session_factory, run_id, urls)

    async def run_url(url: str) -> UnitResult:
        result = await sync_document_url(source, url, session_factory, embed_fn, transport)
        return _unit_result(result)

    return await run_batch(run_id, session_factory, run_url)


async def _discover(
    source: DocumentSource,
    transport: httpx.AsyncBaseTransport | None,
    verification_limit: int | None,
) -> tuple[tuple[str, ...], str | None]:
    seed = source.seed_urls[0]
    async with httpx.AsyncClient(
        transport=transport,
        timeout=15,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        response = await client.get(seed)
    if response.status_code == 429:
        return (), "RATE_LIMITED"
    if 300 <= response.status_code < 400:
        target = urljoin(seed, response.headers.get("Location", ""))
        if "login" in target.lower() or "sso" in target.lower():
            return (), "AUTH_REQUIRED"
    if response.status_code != 200 or not source.allows(str(response.url)):
        return (), f"HTTP_{response.status_code}"

    try:
        if source.name == "gt-registrar":
            urls = discover_registrar_urls(source, response.text)
        elif source.name == "gt-catalog":
            urls = discover_catalog_urls(source, response.text)
        else:
            raise ValueError("unsupported document discovery source")
    except MaxUrlsExceededError:
        return (), "MAX_URLS_EXCEEDED"
    except (ParserError, TypeError, ValueError):
        return (), "DISCOVERY_PARSE_FAILED"
    if verification_limit is not None:
        urls = urls[:verification_limit]
    return (urls, None) if urls else ((), "NO_DOCUMENT_URLS")


def _unit_result(result: DocumentSyncResult) -> UnitResult:
    summary: dict[str, object] = {
        "requests_used": result.requests_used,
        "fetched": result.fetched,
        "changed": result.changed,
        "chunks_indexed": result.chunks_indexed,
    }
    if result.outcome in {DocumentSyncOutcome.INDEXED, DocumentSyncOutcome.UNCHANGED}:
        outcome = UnitOutcome.SUCCEEDED
    elif result.outcome is DocumentSyncOutcome.RATE_LIMITED:
        outcome = UnitOutcome.RATE_LIMITED
    elif result.outcome is DocumentSyncOutcome.AUTH_REQUIRED:
        outcome = UnitOutcome.AUTH_REQUIRED
    elif result.outcome is DocumentSyncOutcome.FETCH_FAILED and (
        result.reason is None or result.reason.startswith("HTTP_5")
    ):
        outcome = UnitOutcome.RETRYABLE
    else:
        outcome = UnitOutcome.FAILED
    return UnitResult(
        outcome,
        summary,
        retry_after_seconds=result.retry_after_seconds,
        reason=result.reason,
    )
