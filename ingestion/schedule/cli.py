from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from app.db.session import SyncSessionLocal
from ingestion.schedule.sync import SyncOutcome, sync_subject


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync one validated public OSCAR subject")
    parser.add_argument("--term", required=True, help="Banner term code, for example 202608")
    parser.add_argument("--subject", required=True, help="Course subject, for example CS")
    parser.add_argument("--probe-course", required=True, help="One course used by the gate probe")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/schedule"),
        help="Ignored directory for safe public-source snapshots",
    )
    args = parser.parse_args(argv)
    result = asyncio.run(
        sync_subject(
            args.term,
            args.subject,
            args.probe_course,
            args.output_dir,
            SyncSessionLocal,
        )
    )
    print(json.dumps(asdict(result), default=str, separators=(",", ":")))
    return 0 if result.outcome is SyncOutcome.PUBLISHED else 2


if __name__ == "__main__":
    raise SystemExit(main())
