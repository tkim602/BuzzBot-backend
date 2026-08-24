from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from itertools import groupby

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import Course, DataVersion, Meeting, Section, SourceSnapshot
from ingestion.schedule.validate import FreshnessState, freshness_state

DAY_CODES = frozenset("MTWRFSU")


@dataclass(frozen=True)
class CourseQuery:
    term_code: str
    subject: str
    course_number: str
    campus: str | None = None
    days: tuple[str, ...] | None = None
    starts_after: time | None = None
    ends_before: time | None = None

    def __post_init__(self) -> None:
        if not self.term_code or not self.subject or not self.course_number:
            raise ValueError("term_code, subject, and course_number are required")
        if self.campus is not None and not self.campus:
            raise ValueError("campus cannot be empty")
        if self.days is not None and (
            not self.days
            or len(set(self.days)) != len(self.days)
            or any(day not in DAY_CODES for day in self.days)
        ):
            raise ValueError("days must contain unique Georgia Tech day codes")
        if (
            self.starts_after is not None
            and self.ends_before is not None
            and self.starts_after > self.ends_before
        ):
            raise ValueError("starts_after cannot be later than ends_before")


@dataclass(frozen=True)
class OfferingMeeting:
    meeting_type: str
    days: str | None
    start_time: time | None
    end_time: time | None
    building: str | None
    room: str | None
    start_date: date
    end_date: date
    is_tba: bool


@dataclass(frozen=True)
class CourseOffering:
    term_code: str
    subject: str
    course_number: str
    title: str
    credits: float
    crn: str
    section_code: str
    campus: str
    schedule_type: str
    instructional_method: str | None
    instructors: tuple[str, ...]
    notes: str | None
    meetings: tuple[OfferingMeeting, ...]
    source_url: str
    data_as_of: datetime
    data_version_id: uuid.UUID
    freshness: FreshnessState


async def lookup_course_offerings(
    session: AsyncSession,
    query: CourseQuery,
) -> list[CourseOffering]:
    matching_meeting = aliased(Meeting)
    source_url = (
        select(SourceSnapshot.source_url)
        .where(SourceSnapshot.data_version_id == DataVersion.id)
        .order_by(SourceSnapshot.fetched_at.desc(), SourceSnapshot.id.desc())
        .limit(1)
        .correlate(DataVersion)
        .scalar_subquery()
    )
    data_as_of = (
        select(SourceSnapshot.fetched_at)
        .where(SourceSnapshot.data_version_id == DataVersion.id)
        .order_by(SourceSnapshot.fetched_at.desc(), SourceSnapshot.id.desc())
        .limit(1)
        .correlate(DataVersion)
        .scalar_subquery()
    )
    filters = [
        DataVersion.status == "PUBLISHED",
        Course.subject == query.subject,
        Course.course_number == query.course_number,
        Section.term_code == query.term_code,
    ]
    if query.campus is not None:
        filters.append(Section.campus == query.campus)

    meeting_filters = [
        matching_meeting.data_version_id == Section.data_version_id,
        matching_meeting.section_id == Section.id,
    ]
    if query.days is not None:
        meeting_filters.extend(matching_meeting.days.contains(day) for day in query.days)
    if query.starts_after is not None:
        meeting_filters.append(matching_meeting.start_time >= query.starts_after)
    if query.ends_before is not None:
        meeting_filters.append(matching_meeting.end_time <= query.ends_before)
    if len(meeting_filters) > 2:
        filters.append(select(1).where(*meeting_filters).exists())

    statement = (
        select(
            DataVersion.id.label("data_version_id"),
            Course.subject,
            Course.course_number,
            Course.title,
            Course.credits,
            Section.id.label("section_id"),
            Section.term_code,
            Section.crn,
            Section.section_code,
            Section.campus,
            Section.schedule_type,
            Section.instructional_method,
            Section.instructors_json,
            Section.notes,
            Meeting.id.label("meeting_id"),
            Meeting.meeting_type,
            Meeting.days,
            Meeting.start_time,
            Meeting.end_time,
            Meeting.building,
            Meeting.room,
            Meeting.start_date,
            Meeting.end_date,
            Meeting.is_tba,
            source_url.label("source_url"),
            data_as_of.label("data_as_of"),
        )
        .join(Course, Course.data_version_id == DataVersion.id)
        .join(
            Section,
            and_(
                Section.data_version_id == Course.data_version_id,
                Section.course_id == Course.id,
            ),
        )
        .outerjoin(
            Meeting,
            and_(
                Meeting.data_version_id == Section.data_version_id,
                Meeting.section_id == Section.id,
            ),
        )
        .where(*filters)
        .order_by(
            Section.crn,
            DataVersion.id,
            Section.id,
            Meeting.start_time.asc().nulls_last(),
            Meeting.meeting_type,
            Meeting.id,
        )
    )
    rows = (await session.execute(statement)).mappings().all()
    now = datetime.now(UTC)
    offerings: list[CourseOffering] = []
    for _, grouped_rows in groupby(
        rows,
        key=lambda row: (row["data_version_id"], row["section_id"]),
    ):
        group = list(grouped_rows)
        first = group[0]
        meetings = tuple(
            OfferingMeeting(
                meeting_type=row["meeting_type"],
                days=row["days"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                building=row["building"],
                room=row["room"],
                start_date=row["start_date"],
                end_date=row["end_date"],
                is_tba=row["is_tba"],
            )
            for row in group
            if row["meeting_id"] is not None
        )
        offerings.append(
            CourseOffering(
                term_code=first["term_code"],
                subject=first["subject"],
                course_number=first["course_number"],
                title=first["title"],
                credits=first["credits"],
                crn=first["crn"],
                section_code=first["section_code"],
                campus=first["campus"],
                schedule_type=first["schedule_type"],
                instructional_method=first["instructional_method"],
                instructors=tuple(first["instructors_json"]),
                notes=first["notes"],
                meetings=meetings,
                source_url=first["source_url"],
                data_as_of=first["data_as_of"],
                data_version_id=first["data_version_id"],
                freshness=freshness_state(first["data_as_of"], now),
            )
        )
    return offerings
