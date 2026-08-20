from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

from ingestion.documents.registry import DocumentSource
from ingestion.probes.cli import USER_AGENT
from ingestion.probes.core import ProbeBudget, ProbeSession


class DocumentProbeStatus(StrEnum):
    READY = "READY"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    DISALLOWED_REDIRECT = "DISALLOWED_REDIRECT"
    FETCH_FAILED = "FETCH_FAILED"
    PARSE_FAILED = "PARSE_FAILED"


@dataclass(frozen=True)
class DocumentProbeResult:
    source: str
    status: DocumentProbeStatus
    requests_used: int
    source_url: str
    title: str | None = None
    reason: str | None = None


async def probe_document_source(
    source: DocumentSource,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DocumentProbeResult:
    budget = ProbeBudget(max_requests=1, max_records=1)
    seed = source.seed_urls[0]
    async with httpx.AsyncClient(
        transport=transport,
        timeout=budget.timeout_seconds,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        session = ProbeSession(client, budget)
        response = await session.get(seed, retry_transient=False, follow_redirects=False)

    if response.status_code == 429:
        status, reason = DocumentProbeStatus.RATE_LIMITED, "HTTP_429"
    elif 300 <= response.status_code < 400:
        location = response.redirect_urls[-1] if response.redirect_urls else ""
        target = urljoin(seed, location)
        if "login" in target.lower() or "sso" in target.lower():
            status, reason = DocumentProbeStatus.AUTH_REQUIRED, "AUTH_REDIRECT"
        elif not source.allows(target):
            status, reason = DocumentProbeStatus.DISALLOWED_REDIRECT, "REDIRECT_OUTSIDE_ROOT"
        else:
            status, reason = DocumentProbeStatus.FETCH_FAILED, "REDIRECT_NOT_FOLLOWED"
    elif response.status_code != 200:
        status, reason = (
            DocumentProbeStatus.FETCH_FAILED,
            response.error or f"HTTP_{response.status_code}",
        )
    elif response.content_type not in {"text/html", "application/xhtml+xml"}:
        status, reason = DocumentProbeStatus.PARSE_FAILED, "UNSUPPORTED_CONTENT_TYPE"
    else:
        title, body_length = _document_shape(response.body)
        if body_length < 100:
            status, reason = DocumentProbeStatus.PARSE_FAILED, "BODY_TOO_SMALL"
        else:
            return DocumentProbeResult(
                source.name,
                DocumentProbeStatus.READY,
                session.requests_used,
                seed,
                title,
            )

    return DocumentProbeResult(source.name, status, session.requests_used, seed, reason=reason)


def _document_shape(body: str) -> tuple[str | None, int]:
    try:
        root = lxml_html.fromstring(body)
    except (TypeError, ValueError):
        return None, 0
    title_parts = root.xpath("//title/text() | //h1[1]//text()")
    title = " ".join(str(part).strip() for part in title_parts if str(part).strip()) or None
    text = " ".join(root.text_content().split())
    return title, len(text)
