"""Common Crawl ingestion pipeline — CDX query → WARC fetch → extract → index."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import structlog
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
load_dotenv()

from ingestion.commoncrawl.cdx_client import DOMAIN_ALLOWLIST, query_cdx
from ingestion.commoncrawl.warc_fetch import fetch_warc_record
from ingestion.extract import extract_content
from ingestion.chunk import chunk_text
from ingestion.normalize import normalize_url, content_hash
from ingestion.index import (
    get_embedding_function,
    index_chunks,
    upsert_document,
    upsert_source,
)
from db.session import SyncSessionLocal

logger = structlog.get_logger(__name__)


async def run_commoncrawl_pipeline(max_per_domain: int = 50) -> dict:
    """Run Common Crawl pipeline for allowlisted domains."""
    if not os.getenv("ENABLE_COMMONCRAWL", "false").lower() == "true":
        logger.info("Common Crawl disabled")
        return {"status": "disabled"}

    embed_fn = get_embedding_function()
    session = SyncSessionLocal()
    stats = {"domains_processed": 0, "documents_indexed": 0, "errors": []}

    try:
        for domain in DOMAIN_ALLOWLIST:
            logger.info("processing domain", domain=domain)
            records = await query_cdx(f"{domain}/*", max_results=max_per_domain)

            source_id = upsert_source(
                session,
                name=f"commoncrawl-{domain}",
                base_url=f"https://{domain}",
                allowed=True,
                reason=f"Common Crawl archive for {domain}",
            )

            for record in records:
                warc = await fetch_warc_record(record)
                if not warc or not warc.html:
                    continue

                extracted = extract_content(warc.url, warc.html)
                if not extracted.success:
                    continue

                canonical = normalize_url(warc.url)
                c_hash = content_hash(extracted.text)

                doc_id = upsert_document(
                    session, source_id, canonical, extracted.title,
                    extracted.text, c_hash,
                )

                chunks = chunk_text(
                    extracted.text,
                    metadata={"url": canonical, "source": f"commoncrawl-{domain}"},
                )

                from datetime import datetime, timezone
                index_chunks(
                    session, doc_id, source_id, chunks, canonical,
                    extracted.title, None, datetime.now(timezone.utc), embed_fn,
                )
                stats["documents_indexed"] += 1

            stats["domains_processed"] += 1

        session.commit()
    except Exception as exc:
        session.rollback()
        stats["errors"].append(str(exc))
        logger.error("Common Crawl pipeline error", error=str(exc))
    finally:
        session.close()

    return stats


if __name__ == "__main__":
    asyncio.run(run_commoncrawl_pipeline())
