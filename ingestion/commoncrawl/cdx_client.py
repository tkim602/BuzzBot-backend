"""Common Crawl CDX API client — query for archived URLs."""

from __future__ import annotations

import os

import httpx
import structlog

logger = structlog.get_logger(__name__)

CDX_API = "https://index.commoncrawl.org"

# Safety: only these domains are allowed
DOMAIN_ALLOWLIST = [
    "gatech.edu",
    "registrar.gatech.edu",
    "catalog.gatech.edu",
]

# Explicit blocklist
DOMAIN_BLOCKLIST = [
    "ratemyprofessors.com",
    "www.ratemyprofessors.com",
]


def _validate_domain(url: str) -> bool:
    """Ensure URL domain is in allowlist and not in blocklist."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if any(blocked in domain for blocked in DOMAIN_BLOCKLIST):
        return False
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in DOMAIN_ALLOWLIST)


async def query_cdx(
    url_pattern: str,
    collection: str | None = None,
    max_results: int = 100,
) -> list[dict]:
    """Query Common Crawl CDX for matching URLs.

    Args:
        url_pattern: URL or wildcard (e.g., "registrar.gatech.edu/*")
        collection: CC collection ID (e.g., "CC-MAIN-2024-10"). Uses latest if None.
        max_results: Max records to return.
    """
    if not _validate_domain(f"https://{url_pattern.split('/')[0]}"):
        logger.error("domain not in allowlist", pattern=url_pattern)
        return []

    collection = collection or os.getenv("COMMONCRAWL_COLLECTION", "CC-MAIN-2024-10")
    endpoint = f"{CDX_API}/{collection}-index"

    params = {
        "url": url_pattern,
        "output": "json",
        "limit": str(max_results),
    }

    results: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(endpoint, params=params)
            resp.raise_for_status()
            for line in resp.text.strip().split("\n"):
                if line.strip():
                    import json

                    record = json.loads(line)
                    if _validate_domain(record.get("url", "")):
                        results.append(record)
    except Exception as exc:
        logger.error("CDX query failed", pattern=url_pattern, error=str(exc))

    logger.info("CDX query", pattern=url_pattern, results=len(results))
    return results
