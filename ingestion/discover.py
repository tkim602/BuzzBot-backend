"""URL discovery — robots.txt + sitemap parsing + pattern filtering."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import structlog
from lxml import etree

logger = structlog.get_logger(__name__)

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@dataclass
class SourceConfig:
    name: str
    base_url: str
    allowed: bool
    reason: str
    sitemap_url: str | None
    include_patterns: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)
    max_urls: int = 200


async def check_robots(base_url: str, user_agent: str = "BuzzBot/1.0") -> RobotFileParser:
    """Fetch and parse robots.txt for the given base URL."""
    robots_url = urljoin(base_url, "/robots.txt")
    rp = RobotFileParser()
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(robots_url)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                logger.info("robots.txt loaded", url=robots_url)
            else:
                logger.warning("robots.txt not found, assuming all allowed", url=robots_url)
    except Exception as exc:
        logger.warning("robots.txt fetch failed", url=robots_url, error=str(exc))
    return rp


async def parse_sitemap(sitemap_url: str) -> list[str]:
    """Parse a sitemap (or sitemap index) and return list of page URLs."""
    urls: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(sitemap_url)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("sitemap fetch failed", url=sitemap_url, error=str(exc))
        return urls

    try:
        root = etree.fromstring(resp.content)
    except Exception as exc:
        logger.warning("sitemap parse failed", url=sitemap_url, error=str(exc))
        return urls

    # Sitemap index — recurse
    sitemap_locs = root.findall(".//sm:sitemap/sm:loc", namespaces=SITEMAP_NS)
    if sitemap_locs:
        for loc in sitemap_locs:
            if loc.text:
                child_urls = await parse_sitemap(loc.text.strip())
                urls.extend(child_urls)
        return urls

    # Regular sitemap
    url_locs = root.findall(".//sm:url/sm:loc", namespaces=SITEMAP_NS)
    for loc in url_locs:
        if loc.text:
            urls.append(loc.text.strip())
    logger.info("sitemap parsed", url=sitemap_url, count=len(urls))
    return urls


def filter_urls(
    urls: list[str],
    base_url: str,
    include_patterns: list[str],
    exclude_patterns: list[str],
    robots: RobotFileParser,
    max_urls: int,
    user_agent: str = "BuzzBot/1.0",
) -> list[str]:
    """Filter discovered URLs by include/exclude patterns and robots.txt."""
    filtered: list[str] = []
    parsed_base = urlparse(base_url)

    for url in urls:
        parsed = urlparse(url)

        # Must be same domain
        if parsed.netloc and parsed.netloc != parsed_base.netloc:
            continue

        path = parsed.path

        # Robots check
        if not robots.can_fetch(user_agent, url):
            logger.debug("blocked by robots.txt", url=url)
            continue

        # Exclude patterns
        excluded = False
        for pat in exclude_patterns:
            if fnmatch.fnmatch(path, pat) or pat in path:
                excluded = True
                break
        if excluded:
            continue

        # Include patterns (if specified, URL must match at least one)
        if include_patterns:
            matched = any(
                fnmatch.fnmatch(path, f"*{pat}*") or pat in path for pat in include_patterns
            )
            if not matched:
                continue

        filtered.append(url)

    # De-duplicate and cap
    seen: set[str] = set()
    deduped: list[str] = []
    for u in filtered:
        if u not in seen:
            seen.add(u)
            deduped.append(u)

    return deduped[:max_urls]


async def discover_urls(source: SourceConfig) -> list[str]:
    """Full discovery pipeline for a single source."""
    if not source.allowed:
        logger.info("source not allowed, skipping", source=source.name, reason=source.reason)
        return []

    logger.info("discovering URLs", source=source.name)

    robots = await check_robots(source.base_url)

    urls: list[str] = []
    if source.sitemap_url:
        urls = await parse_sitemap(source.sitemap_url)

    if not urls:
        logger.info("no sitemap URLs found, using base_url", source=source.name)
        urls = [source.base_url]

    filtered = filter_urls(
        urls=urls,
        base_url=source.base_url,
        include_patterns=source.include_patterns,
        exclude_patterns=source.exclude_patterns,
        robots=robots,
        max_urls=source.max_urls,
    )

    logger.info("discovery complete", source=source.name, total=len(urls), filtered=len(filtered))
    return filtered
