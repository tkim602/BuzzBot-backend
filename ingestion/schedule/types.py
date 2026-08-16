from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time


@dataclass(frozen=True)
class NormalizedCourse:
    subject: str
    course_number: str
    title: str
    credits: float


@dataclass(frozen=True)
class NormalizedMeeting:
    meeting_type: str
    days: str
    start_time: time | None
    end_time: time | None
    building: str | None
    room: str | None
    start_date: date
    end_date: date
    is_tba: bool


@dataclass(frozen=True)
class NormalizedSection:
    term_code: str
    term_name: str
    crn: str
    course_key: tuple[str, str]
    section_code: str
    campus: str
    schedule_type: str
    instructors: tuple[str, ...]
    meetings: tuple[NormalizedMeeting, ...]


@dataclass(frozen=True)
class ParseFailure:
    error_code: str
    record_id: str
    message: str
