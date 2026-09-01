from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.db.session import SyncSessionLocal
from ingestion.orchestration import (
    RunSummary,
    UnitOutcome,
    UnitResult,
    create_run,
    fail_run,
    load_run_summary,
    pause_run,
    plan_run,
    reset_failed_units,
    run_batch,
)
from ingestion.probes.cli import USER_AGENT
from ingestion.probes.core import ProbeBudget, ProbeSession, ProbeStatus, write_probe_artifacts
from ingestion.probes.oscar import probe_oscar
from ingestion.schedule.oscar import SUBJECT_RE, discover_subjects
from ingestion.schedule.sync import PROVIDER, SyncOutcome, SyncResult, collect_subject

SessionFactory = Callable[[], AbstractContextManager[Session]]


async def probe_provider(
    term: str,
    subject: str,
    course: str,
    output_dir: Path,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[ProbeStatus, int | None]:
    budget = ProbeBudget(max_requests=1)
    async with httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(budget.timeout_seconds),
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        session = ProbeSession(client, budget)
        result, response = await probe_oscar(session, term, subject, course)
    write_probe_artifacts(result, response, output_dir)
    return result.status, result.retry_after_seconds


async def sync_oscar_term(
    *,
    term: str | None = None,
    run_id: uuid.UUID | None = None,
    resume: bool = False,
    subjects: Sequence[str] = (),
    retry_failed: Sequence[str] = (),
    probe_subject: str = "CS",
    probe_course: str = "7650",
    concurrency: int = 2,
    retry_limit: int = 2,
    output_dir: Path = Path("artifacts/schedule"),
    session_factory: SessionFactory = SyncSessionLocal,
    transport: httpx.AsyncBaseTransport | None = None,
) -> RunSummary:
    requested = _subjects(subjects)
    retry_units = _subjects(retry_failed)
    if resume:
        if run_id is None or term is not None or requested:
            raise ValueError("resume requires only run_id and optional retry_failed units")
        summary = load_run_summary(session_factory, run_id)
        if summary.provider != PROVIDER:
            raise ValueError("run provider is not public-oscar")
        term = str(summary.scope.get("term", ""))
        if not re.fullmatch(r"\d{6}", term) or not summary.planned_units:
            return fail_run(session_factory, run_id, "MANIFEST_INVALID")
        reset_failed_units(session_factory, run_id, retry_units)
    else:
        if run_id is not None or term is None or not re.fullmatch(r"\d{6}", term):
            raise ValueError("a fresh run requires a six-digit term and no run_id")
        scope: dict[str, object] = {
            "term": term,
            "selection": "explicit" if requested else "all",
        }
        run_id = create_run(
            session_factory,
            PROVIDER,
            scope,
            concurrency=concurrency,
            retry_limit=retry_limit,
        )

    probe_status, _retry_after = await probe_provider(
        term,
        probe_subject,
        probe_course,
        output_dir,
        transport,
    )
    if probe_status is not ProbeStatus.READY:
        if resume and probe_status is ProbeStatus.RATE_LIMITED:
            return pause_run(session_factory, run_id, "RATE_LIMITED")
        return fail_run(session_factory, run_id, probe_status.value)

    if not resume:
        discovery = await discover_subjects(term, transport)
        if discovery.status is not ProbeStatus.READY:
            return fail_run(session_factory, run_id, discovery.status.value)
        if requested and not set(requested) <= set(discovery.subjects):
            return fail_run(session_factory, run_id, "SUBJECT_NOT_OFFERED")
        plan_run(session_factory, run_id, requested or discovery.subjects)

    async def run_unit(subject: str) -> UnitResult:
        result = await collect_subject(
            term,
            subject,
            output_dir,
            session_factory,
            transport,
        )
        return _unit_result(result)

    return await run_batch(run_id, session_factory, run_unit)


def _subjects(values: Sequence[str]) -> tuple[str, ...]:
    subjects = tuple(value.strip().upper() for value in values if value.strip())
    if len(set(subjects)) != len(subjects) or any(
        SUBJECT_RE.fullmatch(item) is None for item in subjects
    ):
        raise ValueError("subjects must be unique 2-8 character OSCAR subject codes")
    return subjects


def _unit_result(result: SyncResult) -> UnitResult:
    summary: dict[str, object] = {
        "requests_used": result.requests_used,
        "records_fetched": result.records_fetched,
        "records_parsed": result.records_parsed,
        "failures": result.failures,
        "courses": result.courses,
        "sections": result.sections,
        "meetings": result.meetings,
        "verified_empty": result.outcome is SyncOutcome.VERIFIED_EMPTY,
    }
    if result.outcome in {SyncOutcome.PUBLISHED, SyncOutcome.VERIFIED_EMPTY}:
        outcome = UnitOutcome.SUCCEEDED
    elif result.outcome is SyncOutcome.RATE_LIMITED:
        outcome = UnitOutcome.RATE_LIMITED
    elif result.outcome is SyncOutcome.AUTH_REQUIRED:
        outcome = UnitOutcome.AUTH_REQUIRED
    elif result.outcome is SyncOutcome.FETCH_FAILED and (
        result.reason is None
        or result.reason.startswith("HTTP_5")
        or not result.reason.startswith("HTTP_")
    ):
        outcome = UnitOutcome.RETRYABLE
    else:
        outcome = UnitOutcome.FAILED
    return UnitResult(
        outcome,
        summary,
        result.retry_after_seconds,
        result.version_id,
        result.reason,
    )


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item for item in value.split(",") if item.strip())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync an immutable public OSCAR term manifest")
    parser.add_argument("--term", help="Banner term code for a fresh run")
    parser.add_argument("--run-id", type=uuid.UUID, help="Existing immutable manifest")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--subjects", type=_csv, default=(), help="Explicit bounded CSV subset")
    parser.add_argument("--retry-failed", type=_csv, default=(), help="Failed unit CSV")
    parser.add_argument("--probe-subject", default="CS")
    parser.add_argument("--probe-course", default="7650")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--rate-limit-retries", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/schedule"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = asyncio.run(
            sync_oscar_term(
                term=args.term,
                run_id=args.run_id,
                resume=args.resume,
                subjects=args.subjects,
                retry_failed=args.retry_failed,
                probe_subject=args.probe_subject,
                probe_course=args.probe_course,
                concurrency=args.concurrency,
                retry_limit=args.rate_limit_retries,
                output_dir=args.output_dir,
            )
        )
    except ValueError as exc:
        _parser().error(str(exc))
    print(json.dumps(asdict(summary), default=str, separators=(",", ":")))
    return 0 if summary.status == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
