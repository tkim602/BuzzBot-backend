from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from ingestion.probes.core import (
    ProbeBudget,
    ProbeResult,
    ProbeSession,
    ProbeStatus,
    write_probe_artifacts,
)
from ingestion.probes.oscar import probe_oscar

USER_AGENT = "BuzzBot/1.0 (+https://github.com/buzzbot; educational project)"


async def run_oscar_probe(
    term: str,
    subject: str,
    course: str,
    output_dir: Path,
    transport: httpx.AsyncBaseTransport | None = None,
    budget: ProbeBudget | None = None,
) -> ProbeResult:
    budget = budget or ProbeBudget()
    async with httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(budget.timeout_seconds),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        result, response = await probe_oscar(
            ProbeSession(client, budget),
            term,
            subject,
            course,
        )
    write_probe_artifacts(result, response, output_dir)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded public-source probes")
    subparsers = parser.add_subparsers(dest="provider", required=True)
    oscar = subparsers.add_parser("oscar", help="Probe one public OSCAR course listing")
    oscar.add_argument("--term", required=True, help="Banner term code, for example 202608")
    oscar.add_argument("--subject", required=True, help="Course subject, for example CS")
    oscar.add_argument("--course", required=True, help="Course number, for example 7650")
    oscar.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/probes"),
        help="Ignored directory for safe probe artifacts",
    )
    args = parser.parse_args()

    result = asyncio.run(
        run_oscar_probe(
            term=args.term,
            subject=args.subject,
            course=args.course,
            output_dir=args.output_dir,
        )
    )
    payload = {**asdict(result), "status": result.status.value}
    print(json.dumps(payload, indent=2, default=str))
    return 0 if result.status is ProbeStatus.READY else 2


if __name__ == "__main__":
    raise SystemExit(main())
