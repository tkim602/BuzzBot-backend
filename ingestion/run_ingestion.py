"""Main ingestion pipeline orchestrator."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import structlog
import yaml

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
TERM_RE = re.compile(r"\b(Spring|Summer|Fall)\s+(20\d{2})\b", re.IGNORECASE)
MONTH_DAY_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2}(?:,\s*20\d{2})?\b",
    re.IGNORECASE,
)
NUMERIC_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/20\d{2}\b")


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
                seed_urls=s.get("seed_urls", []),
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


def _extract_dates(text: str, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in MONTH_DAY_RE.findall(text):
        normalized = str(match).strip()
        if normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        out.append(normalized)
        if len(out) >= limit:
            return out
    for match in NUMERIC_DATE_RE.findall(text):
        if match.lower() in seen:
            continue
        seen.add(match.lower())
        out.append(match)
        if len(out) >= limit:
            break
    return out


def _extract_terms(text: str, limit: int = 4) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for semester, year in TERM_RE.findall(text):
        term = f"{semester.capitalize()} {year}"
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _infer_content_type(url: str, title: str | None, has_tables: bool) -> str:
    target = f"{url} {title or ''}".lower()
    if has_tables or "calendar" in target:
        return "calendar_event"
    if any(k in target for k in ("course", "catalog", "curriculum", "degree")):
        return "course_description"
    if any(k in target for k in ("faq", "questions")):
        return "faq"
    if any(k in target for k in ("policy", "deadline", "registration")):
        return "policy"
    return "web_content"


def _merge_metadata(base: dict, text: str) -> dict:
    merged = dict(base)
    dates = _extract_dates(text)
    terms = _extract_terms(text)
    if dates:
        merged["date_mentioned"] = dates
    if terms:
        merged["term_relevance"] = terms
    return merged


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
                    session,
                    result.url,
                    source_id,
                    None,
                    None,
                    None,
                    status="error",
                    error=result.error,
                )
                continue

            stats["fetched"] += 1

            # Extract
            extracted = extract_content(result.url, result.html)
            if not extracted.success or not extracted.text:
                stats["failed"].append({"url": result.url, "error": "extraction failed"})
                update_fetch_state(
                    session,
                    result.url,
                    source_id,
                    result.etag,
                    result.last_modified,
                    None,
                    status="extract_failed",
                )
                continue

            # Normalize
            canonical = normalize_url(result.url)
            c_hash = content_hash(extracted.text)
            headings = extract_headings(extracted.text)

            # Upsert document
            doc_id = upsert_document(
                session,
                source_id,
                canonical,
                extracted.title,
                extracted.text,
                c_hash,
                result.etag,
                result.last_modified,
            )

            # Chunk
            now = datetime.now(UTC)
            base_content_type = _infer_content_type(
                canonical,
                extracted.title,
                has_tables=bool(extracted.table_rows),
            )
            base_meta = {
                "url": canonical,
                "title": extracted.title,
                "source": source.name,
                "fetched_at": now.isoformat(),
                "content_type": base_content_type,
            }
            chunks = chunk_text(
                extracted.text,
                chunk_size=500,
                chunk_overlap=80,
                metadata=base_meta,
            )
            for c in chunks:
                c.metadata = _merge_metadata(c.metadata, c.text)

            # Add table rows as separate chunks to preserve row-wise key/value relationships.
            for row in extracted.table_rows:
                table_meta = _merge_metadata(
                    {
                        **base_meta,
                        "content_type": "table_row",
                        "table_index": row.get("table_index"),
                        "row_index": row.get("row_index"),
                    },
                    row.get("text", ""),
                )
                table_chunks = chunk_text(
                    row.get("text", ""),
                    chunk_size=200,
                    chunk_overlap=20,
                    min_chunk_size=5,
                    metadata=table_meta,
                )
                chunks.extend(table_chunks)

            # Index
            num_indexed = index_chunks(
                session,
                doc_id,
                source_id,
                chunks,
                canonical,
                extracted.title,
                "\n".join(headings) if headings else None,
                now,
                embed_fn,
            )
            stats["indexed"] += num_indexed

            # Update fetch state
            update_fetch_state(
                session,
                result.url,
                source_id,
                result.etag,
                result.last_modified,
                c_hash,
                status="success",
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
            {"run_at": datetime.now(UTC).isoformat(), "sources": manifest},
            f,
            indent=2,
        )
    with open(ARTIFACTS_DIR / "failed_urls.json", "w") as f:
        json.dump(all_failures, f, indent=2)

    logger.info("=== Ingestion complete ===", artifacts=str(ARTIFACTS_DIR))


if __name__ == "__main__":
    asyncio.run(main())
