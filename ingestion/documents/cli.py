from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict

from ingestion.documents.probe import DocumentProbeStatus, probe_document_source
from ingestion.documents.registry import load_document_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe or sync one controlled GT document source")
    parser.add_argument("command", choices=("probe", "sync"))
    parser.add_argument("--source", required=True)
    args = parser.parse_args(argv)
    sources = {source.name: source for source in load_document_sources()}
    if args.source not in sources:
        parser.error(f"unknown source: {args.source}")
    source = sources[args.source]

    if args.command == "probe":
        result = asyncio.run(probe_document_source(source))
        print(json.dumps(asdict(result), default=str, separators=(",", ":")))
        return 0 if result.status is DocumentProbeStatus.READY else 2

    from db.session import SyncSessionLocal
    from ingestion.documents.sync import sync_document_source
    from ingestion.index import get_embedding_function

    sync_result = asyncio.run(
        sync_document_source(source, SyncSessionLocal, get_embedding_function())
    )
    print(json.dumps(asdict(sync_result), default=str, separators=(",", ":")))
    return 0 if sync_result.outcome.value in {"INDEXED", "UNCHANGED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
