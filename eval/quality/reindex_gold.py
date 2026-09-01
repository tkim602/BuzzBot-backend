from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from app.db.session import SyncSessionLocal
from eval.quality.schema import GoldCase, load_manifest_cases
from ingestion.documents.registry import DocumentSource, load_document_sources
from ingestion.documents.sync import sync_document_url
from ingestion.index import get_embedding_function


def gold_units(
    cases: list[GoldCase], sources: tuple[DocumentSource, ...]
) -> tuple[tuple[DocumentSource, str], ...]:
    by_name = {source.name: source for source in sources}
    units: dict[tuple[str, str], tuple[DocumentSource, str]] = {}
    for case in cases:
        candidates = [by_name[name] for name in case.gold_sources if name in by_name]
        for url in case.gold_urls:
            source = next((item for item in candidates if item.allows(url)), None)
            if source is None:
                raise ValueError(f"{case.id}: no registered gold source allows {url}")
            units[(source.name, url)] = (source, url)
    return tuple(units.values())


async def reindex(manifest: Path) -> dict[str, object]:
    units = gold_units(load_manifest_cases(manifest), load_document_sources())
    embed = get_embedding_function()
    results = []
    for source, url in units:
        result = await sync_document_url(source, url, SyncSessionLocal, embed)
        results.append(asdict(result))
        print(json.dumps(results[-1], default=str, separators=(",", ":")))
    succeeded = sum(row["outcome"] in {"INDEXED", "UNCHANGED"} for row in results)
    return {
        "planned": len(units),
        "succeeded": succeeded,
        "failed": len(units) - succeeded,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely reindex fixed quality-gold documents")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    summary = asyncio.run(reindex(args.manifest))
    print(json.dumps(summary, default=str, separators=(",", ":")))
    raise SystemExit(0 if summary["failed"] == 0 else 2)


if __name__ == "__main__":
    main()
