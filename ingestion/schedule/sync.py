from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlencode

import httpx
from lxml.etree import ParserError
from sqlalchemy.orm import Session

from ingestion.probes.cli import USER_AGENT
from ingestion.probes.core import ProbeBudget, ProbeSession, ProbeStatus, write_probe_artifacts
from ingestion.probes.oscar import (
    AUTH_HOSTS,
    OSCAR_LISTING_URL,
    parse_schedule_listing,
    probe_oscar,
)
from ingestion.schedule.normalize import normalize_sections
from ingestion.schedule.repository import SafeSnapshot, publish_collection
from ingestion.schedule.types import ParseFailure
from ingestion.schedule.validate import CollectionPlan, validate_collection

PROVIDER = "public-oscar"
PARSER_VERSION = "oscar-v1"


class SyncOutcome(StrEnum):
    PUBLISHED = "PUBLISHED"
    PROBE_FAILED = "PROBE_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FETCH_FAILED = "FETCH_FAILED"
    PARSE_FAILED = "PARSE_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"


@dataclass(frozen=True)
class SyncResult:
    outcome: SyncOutcome
    probe_status: ProbeStatus
    requests_used: int
    records_fetched: int = 0
    records_parsed: int = 0
    failures: int = 0
    courses: int = 0
    sections: int = 0
    meetings: int = 0
    version_id: uuid.UUID | None = None
    reason: str | None = None


def build_subject_listing_url(term: str, subject: str) -> str:
    subject = subject.upper()
    if not re.fullmatch(r"\d{6}", term) or not re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", subject):
        raise ValueError("term must be six digits and subject must be 2-8 alphanumerics")
    query = urlencode(
        {
            "term_in": term,
            "subj_in": subject,
            "crse_in": "",
            "schd_in": "%",
        }
    )
    return f"{OSCAR_LISTING_URL}?{query}"


async def sync_subject(
    term: str,
    subject: str,
    probe_course: str,
    output_dir: Path,
    session_factory: Callable[[], AbstractContextManager[Session]],
    transport: httpx.AsyncBaseTransport | None = None,
) -> SyncResult:
    subject = subject.upper()
    subject_url = build_subject_listing_url(term, subject)
    budget = ProbeBudget()
    async with httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(budget.timeout_seconds),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        probe_session = ProbeSession(client, budget)
        probe_result, probe_response = await probe_oscar(
            probe_session,
            term,
            subject,
            probe_course,
        )
        write_probe_artifacts(probe_result, probe_response, output_dir)
        if probe_result.status is not ProbeStatus.READY:
            return SyncResult(
                SyncOutcome.PROBE_FAILED,
                probe_result.status,
                probe_session.requests_used,
                reason=probe_result.reason,
            )

        response = await probe_session.get(
            subject_url,
            retry_transient=False,
            follow_redirects=False,
        )

    failure = _fetch_failure(response)
    if failure is not None:
        outcome, reason = failure
        return SyncResult(
            outcome,
            probe_result.status,
            probe_session.requests_used,
            reason=reason,
        )

    snapshot = _write_subject_snapshot(response, output_dir)
    try:
        samples, parser_failures = parse_schedule_listing(response.body, max_records=None)
    except (ParserError, TypeError, ValueError) as exc:
        return SyncResult(
            SyncOutcome.PARSE_FAILED,
            probe_result.status,
            probe_session.requests_used,
            reason=type(exc).__name__,
        )
    if not samples and not parser_failures:
        return SyncResult(
            SyncOutcome.PARSE_FAILED,
            probe_result.status,
            probe_session.requests_used,
            reason="NO_SECTIONS",
        )

    courses, sections, normalization_failures = normalize_sections(term, samples)
    failures = [
        ParseFailure(failure.error_code, failure.raw_header[:128], "OSCAR section parse failed")
        for failure in parser_failures
    ]
    failures.extend(normalization_failures)
    fetched = len(samples) + len(parser_failures)
    plan = CollectionPlan(
        term_code=term,
        planned_subjects=(subject,),
        completed_subjects=(subject,),
        failed_units=(),
        records_fetched=fetched,
        records_parsed=len(sections),
    )
    report = validate_collection(plan, courses, sections, failures, snapshot.fetched_at)
    with session_factory() as session:
        version_id = publish_collection(
            session,
            PROVIDER,
            f"{term}:{subject}",
            snapshot,
            plan,
            courses,
            sections,
            failures,
            report,
        )

    return SyncResult(
        SyncOutcome.PUBLISHED if report.valid else SyncOutcome.VALIDATION_FAILED,
        probe_result.status,
        probe_session.requests_used,
        fetched,
        len(sections),
        len(failures),
        len(courses),
        len(sections),
        sum(len(section.meetings) for section in sections),
        version_id,
        None if report.valid else ",".join(issue.code for issue in report.issues),
    )


def _fetch_failure(response) -> tuple[SyncOutcome, str] | None:
    urls = (*response.redirect_urls, response.final_url)
    if any(
        httpx.URL(url).host in AUTH_HOSTS or "/login" in httpx.URL(url).path.lower() for url in urls
    ) or response.status_code in {401, 403}:
        return SyncOutcome.AUTH_REQUIRED, "AUTH_REQUIRED"
    if response.status_code == 429:
        return SyncOutcome.RATE_LIMITED, "HTTP_429"
    if response.status_code != 200:
        return SyncOutcome.FETCH_FAILED, response.error or f"HTTP_{response.status_code}"
    return None


def _write_subject_snapshot(response, output_dir: Path) -> SafeSnapshot:
    output_dir.mkdir(parents=True, exist_ok=True)
    body_path = output_dir / f"subject-{response.sha256}.html"
    body_path.write_text(response.body, encoding="utf-8")
    return SafeSnapshot(
        source_url=response.source_url,
        fetched_at=datetime.fromisoformat(response.fetched_at),
        status_code=response.status_code,
        content_type=response.content_type,
        content_hash=response.sha256,
        parser_version=PARSER_VERSION,
        raw_location=str(body_path),
    )
