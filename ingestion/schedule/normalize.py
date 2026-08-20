from __future__ import annotations

import re
from datetime import datetime, time

from ingestion.probes.oscar import OscarMeetingSample, OscarSectionSample
from ingestion.schedule.types import (
    NormalizedCourse,
    NormalizedMeeting,
    NormalizedSection,
    ParseFailure,
)

ROOM_RE = re.compile(r"\S*\d\S*")


def normalize_sections(
    term_code: str,
    samples: list[OscarSectionSample],
) -> tuple[list[NormalizedCourse], list[NormalizedSection], list[ParseFailure]]:
    courses: dict[tuple[str, str], NormalizedCourse] = {}
    sections: list[NormalizedSection] = []
    failures: list[ParseFailure] = []

    for sample in samples:
        key = (sample.subject, sample.course)
        courses.setdefault(
            key,
            NormalizedCourse(sample.subject, sample.course, sample.title, sample.credits),
        )
        try:
            meetings = tuple(_normalize_meeting(meeting) for meeting in sample.meetings)
        except ValueError as exc:
            failures.append(ParseFailure("MEETING_INVALID", sample.crn, str(exc)))
            continue

        sections.append(
            NormalizedSection(
                term_code=term_code,
                term_name=sample.term_name,
                crn=sample.crn,
                course_key=key,
                section_code=sample.section,
                campus=sample.campus,
                schedule_type=sample.schedule_type or _schedule_type(sample.meetings),
                instructors=tuple(
                    dict.fromkeys(
                        meeting.instructor for meeting in sample.meetings if meeting.instructor
                    )
                ),
                meetings=meetings,
            )
        )

    return list(courses.values()), sections, failures


def _normalize_meeting(sample: OscarMeetingSample) -> NormalizedMeeting:
    start_time, end_time, is_tba = _time_range(sample.time)
    building, room = _location(sample.location)
    start_date, end_date = (
        datetime.strptime(value.strip(), "%b %d, %Y").date()
        for value in sample.date_range.split(" - ", maxsplit=1)
    )
    return NormalizedMeeting(
        meeting_type=sample.meeting_type,
        days=sample.days,
        start_time=start_time,
        end_time=end_time,
        building=building,
        room=room,
        start_date=start_date,
        end_date=end_date,
        is_tba=is_tba,
    )


def _time_range(value: str) -> tuple[time | None, time | None, bool]:
    if value.upper() == "TBA":
        return None, None, True
    start, end = value.split(" - ", maxsplit=1)
    return (
        datetime.strptime(start.strip(), "%I:%M %p").time(),
        datetime.strptime(end.strip(), "%I:%M %p").time(),
        False,
    )


def _location(value: str) -> tuple[str | None, str | None]:
    if not value or value.upper() == "TBA":
        return None, None
    building, separator, room = value.rpartition(" ")
    if separator and ROOM_RE.fullmatch(room):
        return building, room
    return value, None


def _schedule_type(meetings: tuple[OscarMeetingSample, ...]) -> str:
    return meetings[0].schedule_type.removesuffix("*").strip() if meetings else ""
