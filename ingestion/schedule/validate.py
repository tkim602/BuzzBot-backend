from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from ingestion.schedule.types import NormalizedCourse, NormalizedSection, ParseFailure


class FreshnessState(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class CollectionPlan:
    term_code: str
    planned_subjects: tuple[str, ...]
    completed_subjects: tuple[str, ...]
    failed_units: tuple[str, ...]
    records_fetched: int
    records_parsed: int


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    record_id: str | None
    message: str


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    parse_success_rate: float
    issues: tuple[ValidationIssue, ...]


def freshness_state(
    fetched_at: datetime,
    now: datetime,
    target_hours: float = 6,
    max_hours: float = 24,
    historical: bool = False,
) -> FreshnessState:
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetched_at must be timezone-aware")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if target_hours < 0 or max_hours < target_hours:
        raise ValueError("freshness hours must satisfy 0 <= target_hours <= max_hours")

    age = now - fetched_at
    if age < timedelta(0):
        raise ValueError("fetched_at cannot be in the future")
    if age <= timedelta(hours=target_hours):
        return FreshnessState.CURRENT
    if historical or age <= timedelta(hours=max_hours):
        return FreshnessState.STALE
    return FreshnessState.EXPIRED


def validate_collection(
    plan: CollectionPlan,
    courses: Sequence[NormalizedCourse],
    sections: Sequence[NormalizedSection],
    failures: Sequence[ParseFailure],
    fetched_at: datetime,
) -> ValidationReport:
    issues: list[ValidationIssue] = []

    if set(plan.completed_subjects) != set(plan.planned_subjects) or plan.failed_units:
        issues.append(
            ValidationIssue(
                "COLLECTION_INCOMPLETE",
                None,
                "Not every planned subject completed successfully",
            )
        )

    counts_valid = (
        plan.records_fetched >= 0
        and 0 <= plan.records_parsed <= plan.records_fetched
        and len(failures) == plan.records_fetched - plan.records_parsed
    )
    parse_success_rate = (
        plan.records_parsed / plan.records_fetched if counts_valid and plan.records_fetched else 0.0
    )
    if not counts_valid:
        issues.append(
            ValidationIssue(
                "RECORD_COUNTS_INVALID",
                None,
                "Fetched, parsed, and failed record counts do not reconcile",
            )
        )
    elif plan.records_fetched and parse_success_rate < 0.99:
        issues.append(
            ValidationIssue(
                "PARSE_RATE_LOW",
                None,
                "Parse success rate is below 99%",
            )
        )

    if not courses or not sections or plan.records_fetched == 0:
        issues.append(ValidationIssue("EMPTY_COLLECTION", None, "Collection has no usable records"))

    course_keys = {(course.subject, course.course_number) for course in courses}
    if len(course_keys) != len(courses):
        issues.append(ValidationIssue("DUPLICATE_COURSE", None, "Course keys must be unique"))

    seen_sections: set[tuple[str, str]] = set()
    for section in sections:
        section_key = (section.term_code, section.crn)
        if section_key in seen_sections:
            issues.append(
                ValidationIssue(
                    "DUPLICATE_CRN",
                    section.crn,
                    "CRN must be unique within a term",
                )
            )
        seen_sections.add(section_key)

        if section.term_code != plan.term_code:
            issues.append(
                ValidationIssue(
                    "TERM_MISMATCH",
                    section.crn,
                    "Section term does not match the collection plan",
                )
            )
        if section.course_key not in course_keys:
            issues.append(
                ValidationIssue(
                    "COURSE_REFERENCE_MISSING",
                    section.crn,
                    "Section does not reference an exact collected course key",
                )
            )

        if not section.meetings:
            issues.append(
                ValidationIssue(
                    "MEETING_MISSING",
                    section.crn,
                    "Section must include a meeting or explicit TBA meeting",
                )
            )
        for meeting in section.meetings:
            has_any_time = meeting.start_time is not None or meeting.end_time is not None
            missing_any_time = meeting.start_time is None or meeting.end_time is None
            if (meeting.is_tba and has_any_time) or (not meeting.is_tba and missing_any_time):
                issues.append(
                    ValidationIssue(
                        "MEETING_INCOMPLETE",
                        section.crn,
                        "Meeting times must be complete or explicitly TBA",
                    )
                )

    now = datetime.now(UTC)
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        issues.append(
            ValidationIssue("FETCHED_AT_INVALID", None, "Fetched timestamp must be timezone-aware")
        )
    elif fetched_at > now:
        issues.append(
            ValidationIssue("FETCHED_AT_FUTURE", None, "Fetched timestamp is in the future")
        )
    elif freshness_state(fetched_at, now) is FreshnessState.EXPIRED:
        issues.append(
            ValidationIssue("COLLECTION_EXPIRED", None, "Collection is older than 24 hours")
        )

    return ValidationReport(not issues, parse_success_rate, tuple(issues))
