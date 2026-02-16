"""Main ingestion pipeline orchestrator."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog
import yaml
from dotenv import load_dotenv

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from db.session import SyncSessionLocal
from ingestion.chunk import chunk_text
from ingestion.discover import SourceConfig, discover_urls
from ingestion.extract import extract_content
from ingestion.fetch import FetchResult, fetch_urls
from ingestion.index import (
    get_embedding_function,
    index_chunks,
    update_fetch_state,
    upsert_document,
    upsert_source,
)
from ingestion.normalize import content_hash, extract_headings, normalize_url

logger = structlog.get_logger(__name__)

SOURCES_YAML = Path(__file__).parent / "sources.yaml"
ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"


def load_sources() -> list[SourceConfig]:
    """Load source configurations from sources.yaml."""
    with open(SOURCES_YAML) as f:
        data = yaml.safe_load(f)

    sources: list[SourceConfig] = []
    for s in data.get("sources", []):
        sources.append(
            SourceConfig(
                name=s["name"],
                base_url=s["base_url"],
                allowed=s.get("allowed", True),
                reason=s.get("reason", ""),
                sitemap_url=s.get("sitemap_url"),
                include_patterns=s.get("include_patterns", []),
                exclude_patterns=s.get("exclude_patterns", []),
                max_urls=s.get("max_urls", 200),
            )
        )
    return sources


async def run_discovery(sources: list[SourceConfig]) -> dict[str, list[str]]:
    """Discover URLs for all sources."""
    results: dict[str, list[str]] = {}
    for source in sources:
        urls = await discover_urls(source)
        results[source.name] = urls
        logger.info("discovered", source=source.name, count=len(urls))
    return results


def process_source(
    source: SourceConfig,
    urls: list[str],
    fetch_results: list[FetchResult],
    embed_fn,
) -> dict:
    """Process fetched pages for a single source — extract, chunk, index."""
    session = SyncSessionLocal()
    stats = {"source": source.name, "fetched": 0, "indexed": 0, "failed": [], "skipped": 0}

    try:
        source_id = upsert_source(
            session, source.name, source.base_url, source.allowed, source.reason
        )

        for result in fetch_results:
            if result.not_modified:
                stats["skipped"] += 1
                continue

            if result.error or not result.html:
                stats["failed"].append({"url": result.url, "error": result.error or "no content"})
                update_fetch_state(
                    session, result.url, source_id, None, None, None,
                    status="error", error=result.error,
                )
                continue

            stats["fetched"] += 1

            # Extract
            extracted = extract_content(result.url, result.html)
            if not extracted.success or not extracted.text:
                stats["failed"].append({"url": result.url, "error": "extraction failed"})
                update_fetch_state(
                    session, result.url, source_id, result.etag, result.last_modified, None,
                    status="extract_failed",
                )
                continue

            # Normalize
            canonical = normalize_url(result.url)
            c_hash = content_hash(extracted.text)
            headings = extract_headings(extracted.text)

            # Upsert document
            doc_id = upsert_document(
                session, source_id, canonical, extracted.title,
                extracted.text, c_hash, result.etag, result.last_modified,
            )

            # Chunk
            now = datetime.now(timezone.utc)
            chunks = chunk_text(
                extracted.text,
                chunk_size=500,
                chunk_overlap=80,
                metadata={
                    "url": canonical,
                    "title": extracted.title,
                    "source": source.name,
                    "fetched_at": now.isoformat(),
                },
            )

            # Index
            num_indexed = index_chunks(
                session, doc_id, source_id, chunks, canonical,
                extracted.title, "\n".join(headings) if headings else None,
                now, embed_fn,
            )
            stats["indexed"] += num_indexed

            # Update fetch state
            update_fetch_state(
                session, result.url, source_id, result.etag,
                result.last_modified, c_hash, status="success",
            )

        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("source processing failed", source=source.name, error=str(exc))
        stats["failed"].append({"url": "N/A", "error": str(exc)})
    finally:
        session.close()

    return stats


async def main() -> None:
    """Run the full ingestion pipeline."""
    logger.info("=== BuzzBot Ingestion Pipeline ===")

    sources = load_sources()
    logger.info("loaded sources", count=len(sources))

    # Discovery phase
    url_map = await run_discovery(sources)

    # Embedding function
    embed_fn = get_embedding_function()

    manifest: list[dict] = []
    all_failures: list[dict] = []

    for source in sources:
        if not source.allowed:
            continue

        urls = url_map.get(source.name, [])
        if not urls:
            logger.info("no URLs for source, skipping", source=source.name)
            continue

        # Fetch phase
        logger.info("fetching", source=source.name, count=len(urls))
        fetch_results = await fetch_urls(urls)

        # Process phase
        stats = process_source(source, urls, fetch_results, embed_fn)
        manifest.append(stats)
        all_failures.extend(stats.get("failed", []))

        logger.info(
            "source complete",
            source=source.name,
            fetched=stats["fetched"],
            indexed=stats["indexed"],
            failed=len(stats["failed"]),
            skipped=stats["skipped"],
        )

    # Write artifacts
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    with open(ARTIFACTS_DIR / "manifest.json", "w") as f:
        json.dump(
            {"run_at": datetime.now(timezone.utc).isoformat(), "sources": manifest},
            f,
            indent=2,
        )
    with open(ARTIFACTS_DIR / "failed_urls.json", "w") as f:
        json.dump(all_failures, f, indent=2)

    logger.info("=== Ingestion complete ===", artifacts=str(ARTIFACTS_DIR))


if __name__ == "__main__":
    asyncio.run(main())
