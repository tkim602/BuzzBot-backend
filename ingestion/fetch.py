"""Async HTTP fetcher with conditional requests, retries, and rate limiting."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)

CONCURRENCY = int(os.getenv("INGEST_CONCURRENCY", "5"))
RATE_LIMIT = float(os.getenv("INGEST_RATE_LIMIT_PER_DOMAIN", "2.0"))

USER_AGENT = "BuzzBot/1.0 (+https://github.com/buzzbot; educational project)"


@dataclass
class FetchResult:
    url: str
    status_code: int
    html: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    error: str | None = None


class DomainRateLimiter:
    """Simple per-domain rate limiter using minimum interval."""

    def __init__(self, min_interval: float = 1.0):
        self._min_interval = min_interval
        self._last_request: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, domain: str) -> None:
        async with self._lock:
            now = time.monotonic()
            last = self._last_request.get(domain, 0.0)
            elapsed = now - last
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request[domain] = time.monotonic()


_rate_limiter = DomainRateLimiter(min_interval=1.0 / RATE_LIMIT if RATE_LIMIT > 0 else 0.5)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FetchResult:
    """Fetch a single URL with conditional GET support."""
    from urllib.parse import urlparse

    domain = urlparse(url).netloc
    await _rate_limiter.wait(domain)

    headers: dict[str, str] = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    resp = await client.get(url, headers=headers, follow_redirects=True)

    if resp.status_code == 304:
        return FetchResult(url=url, status_code=304, not_modified=True)

    if resp.status_code >= 400:
        return FetchResult(url=url, status_code=resp.status_code, error=f"HTTP {resp.status_code}")

    return FetchResult(
        url=url,
        status_code=resp.status_code,
        html=resp.text,
        etag=resp.headers.get("ETag"),
        last_modified=resp.headers.get("Last-Modified"),
    )


async def fetch_urls(
    urls: list[str],
    existing_state: dict[str, dict] | None = None,
) -> list[FetchResult]:
    """Fetch multiple URLs concurrently with rate limiting."""
    existing_state = existing_state or {}
    semaphore = asyncio.Semaphore(CONCURRENCY)
    results: list[FetchResult] = []

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:

        async def _bounded_fetch(url: str) -> FetchResult:
            async with semaphore:
                state = existing_state.get(url, {})
                try:
                    return await _fetch_one(
                        client,
                        url,
                        etag=state.get("etag"),
                        last_modified=state.get("last_modified"),
                    )
                except Exception as exc:
                    logger.error("fetch failed after retries", url=url, error=str(exc))
                    return FetchResult(url=url, status_code=0, error=str(exc))

        tasks = [_bounded_fetch(url) for url in urls]
        results = await asyncio.gather(*tasks)

    return list(results)
