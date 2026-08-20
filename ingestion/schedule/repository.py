from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from db.models import (
    AcademicTerm,
    Course,
    DataVersion,
    IngestionError,
    Meeting,
    Section,
    SourceSnapshot,
)
from ingestion.schedule.types import NormalizedCourse, NormalizedSection, ParseFailure
from ingestion.schedule.validate import CollectionPlan, ValidationReport


@dataclass(frozen=True)
class SafeSnapshot:
    source_url: str
    fetched_at: datetime
    status_code: int
    content_type: str | None
    content_hash: str
    parser_version: str
    raw_location: str


def latest_published_version(
    session: Session,
    provider: str,
    requested_unit: str,
) -> DataVersion | None:
    return session.scalars(
        select(DataVersion)
        .where(
            DataVersion.provider == provider,
            DataVersion.requested_unit == requested_unit,
            DataVersion.status == "PUBLISHED",
        )
        .order_by(DataVersion.published_at.desc(), DataVersion.created_at.desc())
        .limit(1)
    ).first()


def publish_collection(
    session: Session,
    provider: str,
    requested_unit: str,
    snapshot: SafeSnapshot,
    plan: CollectionPlan,
    courses: Sequence[NormalizedCourse],
    sections: Sequence[NormalizedSection],
    failures: Sequence[ParseFailure],
    report: ValidationReport,
) -> uuid.UUID:
    version_id = uuid.uuid4()
    meeting_count = sum(len(section.meetings) for section in sections)
    errors = [
        {
            "id": uuid.uuid4(),
            "data_version_id": version_id,
            "error_code": failure.error_code,
            "record_id": failure.record_id,
            "message": failure.message,
        }
        for failure in failures
    ]
    errors.extend(
        {
            "id": uuid.uuid4(),
            "data_version_id": version_id,
            "error_code": issue.code,
            "record_id": issue.record_id,
            "message": issue.message,
        }
        for issue in report.issues
    )

    with session.begin():
        session.execute(
            insert(DataVersion),
            {
                "id": version_id,
                "provider": provider,
                "requested_unit": requested_unit,
                "status": "STAGED" if report.valid else "FAILED",
                "row_counts_json": {
                    "courses": len(courses),
                    "sections": len(sections),
                    "meetings": meeting_count,
                    "errors": len(errors),
                    "verified_empty_subjects": list(plan.verified_empty_subjects),
                },
            },
        )
        session.execute(
            insert(SourceSnapshot),
            {
                "id": uuid.uuid4(),
                "data_version_id": version_id,
                "provider": provider,
                "source_url": snapshot.source_url,
                "fetched_at": snapshot.fetched_at,
                "status_code": snapshot.status_code,
                "content_type": snapshot.content_type,
                "content_hash": snapshot.content_hash,
                "parser_version": snapshot.parser_version,
                "raw_location": snapshot.raw_location,
                "validation_status": "PASSED" if report.valid else "FAILED",
            },
        )
        if errors:
            session.execute(insert(IngestionError), errors)

        if not report.valid:
            return version_id

        session.execute(
            select(func.pg_advisory_xact_lock(_publication_lock_key(provider, requested_unit)))
        )
        term_ids = _insert_terms(session, version_id, sections)
        course_ids = _insert_courses(session, version_id, courses)
        section_ids = _insert_sections(
            session,
            version_id,
            sections,
            term_ids,
            course_ids,
        )
        _insert_meetings(session, version_id, sections, section_ids)

        previous = latest_published_version(session, provider, requested_unit)
        if previous is not None:
            session.execute(
                update(DataVersion).where(DataVersion.id == previous.id).values(status="SUPERSEDED")
            )
        session.execute(
            update(DataVersion)
            .where(DataVersion.id == version_id)
            .values(status="PUBLISHED", published_at=datetime.now(UTC))
        )
    return version_id


def _publication_lock_key(provider: str, requested_unit: str) -> int:
    digest = hashlib.blake2b(
        f"{provider}\0{requested_unit}".encode(),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


def _insert_terms(
    session: Session,
    version_id: uuid.UUID,
    sections: Sequence[NormalizedSection],
) -> dict[str, uuid.UUID]:
    term_ids: dict[str, uuid.UUID] = {}
    rows: list[dict] = []
    for section in sections:
        if section.term_code in term_ids:
            continue
        term_id = uuid.uuid4()
        term_ids[section.term_code] = term_id
        rows.append(
            {
                "id": term_id,
                "data_version_id": version_id,
                "term_code": section.term_code,
                "display_name": section.term_name,
            }
        )
    if rows:
        session.execute(insert(AcademicTerm), rows)
    return term_ids


def _insert_courses(
    session: Session,
    version_id: uuid.UUID,
    courses: Sequence[NormalizedCourse],
) -> dict[tuple[str, str], uuid.UUID]:
    course_ids: dict[tuple[str, str], uuid.UUID] = {}
    rows: list[dict] = []
    for course in courses:
        course_id = uuid.uuid4()
        key = (course.subject, course.course_number)
        course_ids[key] = course_id
        rows.append(
            {
                "id": course_id,
                "data_version_id": version_id,
                "subject": course.subject,
                "course_number": course.course_number,
                "title": course.title,
                "credits": course.credits,
            }
        )
    if rows:
        session.execute(insert(Course), rows)
    return course_ids


def _insert_sections(
    session: Session,
    version_id: uuid.UUID,
    sections: Sequence[NormalizedSection],
    term_ids: dict[str, uuid.UUID],
    course_ids: dict[tuple[str, str], uuid.UUID],
) -> dict[tuple[str, str], uuid.UUID]:
    section_ids: dict[tuple[str, str], uuid.UUID] = {}
    rows: list[dict] = []
    for section in sections:
        section_id = uuid.uuid4()
        section_ids[(section.term_code, section.crn)] = section_id
        rows.append(
            {
                "id": section_id,
                "data_version_id": version_id,
                "academic_term_id": term_ids[section.term_code],
                "course_id": course_ids[section.course_key],
                "term_code": section.term_code,
                "crn": section.crn,
                "section_code": section.section_code,
                "campus": section.campus,
                "schedule_type": section.schedule_type,
                "instructors_json": list(section.instructors),
            }
        )
    if rows:
        session.execute(insert(Section), rows)
    return section_ids


def _insert_meetings(
    session: Session,
    version_id: uuid.UUID,
    sections: Sequence[NormalizedSection],
    section_ids: dict[tuple[str, str], uuid.UUID],
) -> None:
    rows: list[dict] = []
    for section in sections:
        section_id = section_ids[(section.term_code, section.crn)]
        for meeting in section.meetings:
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "data_version_id": version_id,
                    "section_id": section_id,
                    "meeting_type": meeting.meeting_type,
                    "days": meeting.days or None,
                    "start_time": meeting.start_time,
                    "end_time": meeting.end_time,
                    "start_date": meeting.start_date,
                    "end_date": meeting.end_date,
                    "building": meeting.building,
                    "room": meeting.room,
                    "is_tba": meeting.is_tba,
                }
            )
    if rows:
        session.execute(insert(Meeting), rows)
