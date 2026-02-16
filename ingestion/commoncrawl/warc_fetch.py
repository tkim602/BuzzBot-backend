"""Fetch WARC records from Common Crawl S3."""

from __future__ import annotations

import gzip
from dataclasses import dataclass

import httpx
import structlog

logger = structlog.get_logger(__name__)

CC_S3_BASE = "https://data.commoncrawl.org"


@dataclass
class WarcRecord:
    url: str
    html: str
    status: int


async def fetch_warc_record(cdx_record: dict) -> WarcRecord | None:
    """Fetch a single WARC record given its CDX metadata."""
    filename = cdx_record.get("filename")
    offset = int(cdx_record.get("offset", 0))
    length = int(cdx_record.get("length", 0))
    url = cdx_record.get("url", "")

    if not filename or not length:
        logger.warning("incomplete CDX record", url=url)
        return None

    range_header = f"bytes={offset}-{offset + length - 1}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{CC_S3_BASE}/{filename}",
                headers={"Range": range_header},
            )
            resp.raise_for_status()

            # Decompress gzip
            try:
                raw = gzip.decompress(resp.content)
            except gzip.BadGzipFile:
                raw = resp.content

            # Parse WARC to extract HTML body
            text = raw.decode("utf-8", errors="replace")
            # Find HTTP response body (after double CRLF)
            parts = text.split("\r\n\r\n", 2)
            html = parts[-1] if len(parts) >= 2 else text

            return WarcRecord(url=url, html=html, status=200)

    except Exception as exc:
        logger.error("WARC fetch failed", url=url, error=str(exc))
        return None
