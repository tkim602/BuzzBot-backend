"""Live fetch — polite, robots-compliant fetch for freshness-critical queries."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx
import structlog

from app.core.config import settings
from app.rag.retrieval import RetrievedChunk, rerank_chunks_by_embedding
from ingestion.extract import extract_content
from ingestion.chunk import chunk_text

logger = structlog.get_logger(__name__)

# Safe targets for live fetch (official GT only)
LIVE_FETCH_TARGETS = {
    "registrar_calendar": [
        "https://registrar.gatech.edu/calendar",
        "https://registrar.gatech.edu/registration",
    ],
    "catalog_course": [
        "https://catalog.gatech.edu",
    ],
}

USER_AGENT = "BuzzBot/1.0 (+educational; polite single-page fetch)"
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _overlap_score(query: str, text: str) -> float:
    q_tokens = set(_TOKEN_RE.findall(query.lower()))
    if not q_tokens:
        return 0.0
    t_tokens = set(_TOKEN_RE.findall(text.lower()))
    if not t_tokens:
        return 0.0
    return len(q_tokens & t_tokens) / len(q_tokens)


async def live_fetch_for_query(
    intent: str,
    query: str,
) -> list[RetrievedChunk]:
    """Fetch small set of official pages for freshness-critical queries."""
    if not settings.enable_live_fetch:
        return []

    timeout = settings.live_fetch_timeout
    max_urls = settings.live_fetch_max_urls
    max_chunks = settings.live_fetch_max_chunks
    targets = LIVE_FETCH_TARGETS.get(intent, [])[:max_urls]

    if not targets:
        return []

    chunks: list[RetrievedChunk] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for url in targets:
            try:
                resp = await client.get(url, headers={"User-Agent": USER_AGENT})
                if resp.status_code != 200:
                    continue

                extracted = extract_content(url, resp.text)
                if not extracted.success or not extracted.text:
                    continue

                now = datetime.now(timezone.utc)
                text_chunks = chunk_text(
                    extracted.text,
                    chunk_size=500,
                    chunk_overlap=80,
                    metadata={"url": url, "source": "live_fetch"},
                )

                for tc in text_chunks:
                    chunks.append(
                        RetrievedChunk(
                            chunk_id=f"live-{tc.chunk_hash[:12]}",
                            url=url,
                            title=extracted.title,
                            chunk_text=tc.text,
                            score=_overlap_score(query, tc.text),
                            fetched_at=now.isoformat(),
                            method="live_fetch",
                        )
                    )
            except Exception as exc:
                logger.warning("live fetch failed", url=url, error=str(exc))

    # Rerank with embedding similarity (blended with lexical overlap) to improve precision.
    chunks = await rerank_chunks_by_embedding(query, chunks, alpha=0.75)
    chunks = chunks[:max_chunks]
    logger.info("live fetch complete", intent=intent, chunks=len(chunks))
    return chunks
