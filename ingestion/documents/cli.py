from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import asdict

from ingestion.documents.probe import DocumentProbeStatus, probe_document_source
from ingestion.documents.registry import load_document_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe or sync controlled GT document sources")
    parser.add_argument("command", choices=("probe", "sync", "sync-many"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--run-id", type=uuid.UUID)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verification-limit", type=int)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--rate-limit-retries", type=int, default=2)
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

    if args.command == "sync-many":
        from ingestion.documents.sync_source import sync_document_source_urls

        try:
            summary = asyncio.run(
                sync_document_source_urls(
                    source,
                    SyncSessionLocal,
                    get_embedding_function(),
                    run_id=args.run_id,
                    resume=args.resume,
                    verification_limit=args.verification_limit,
                    concurrency=args.concurrency,
                    retry_limit=args.rate_limit_retries,
                )
            )
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(asdict(summary), default=str, separators=(",", ":")))
        return 0 if summary.status == "COMPLETED" else 2

    sync_result = asyncio.run(
        sync_document_source(source, SyncSessionLocal, get_embedding_function())
    )
    print(json.dumps(asdict(sync_result), default=str, separators=(",", ":")))
    return 0 if sync_result.outcome.value in {"INDEXED", "UNCHANGED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
