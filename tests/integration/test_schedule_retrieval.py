from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DataVersion
from app.retrieval.schedule import CourseQuery, lookup_course_offerings
from ingestion.probes.oscar import parse_schedule_listing
from ingestion.schedule.normalize import normalize_sections
from ingestion.schedule.repository import SafeSnapshot, publish_collection
from ingestion.schedule.types import NormalizedCourse, NormalizedMeeting, NormalizedSection
from ingestion.schedule.validate import CollectionPlan, FreshnessState, ValidationReport

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="set RUN_DB_TESTS=1 for PostgreSQL tests"
)

TERM = "299901"
SUBJECT = "CS"
COURSE = "7650"


def _meeting(
    days: str,
    starts_at: time | None,
    ends_at: time | None,
    *,
    tba: bool = False,
) -> NormalizedMeeting:
    return NormalizedMeeting(
        "Class",
        days,
        starts_at,
        ends_at,
        None if tba else "Klaus",
        None if tba else "1447",
        date(2026, 8, 24),
        date(2026, 12, 17),
        tba,
    )


def _section(
    crn: str,
    campus: str,
    instructor: str,
    meetings: tuple[NormalizedMeeting, ...],
) -> NormalizedSection:
    return NormalizedSection(
        TERM,
        "Fixture Term",
        crn,
        (SUBJECT, COURSE),
        crn[-1],
        campus,
        "Lecture",
        (instructor,),
        meetings,
    )


def _publish(
    provider: str,
    requested_unit: str,
    source_url: str,
    sections: list[NormalizedSection],
    courses: list[NormalizedCourse] | None = None,
) -> uuid.UUID:
    sync_engine = create_engine(settings.database_url_sync)
    try:
        with Session(sync_engine) as session:
            return publish_collection(
                session,
                provider,
                requested_unit,
                SafeSnapshot(
                    source_url,
                    datetime.now(UTC),
                    200,
                    "text/html",
                    "a" * 64,
                    "fixture-v1",
                    "tests/fixtures/oscar_schedule_sample.html",
                ),
                CollectionPlan(
                    TERM,
                    (SUBJECT,),
                    (SUBJECT,),
                    (),
                    len(sections),
                    len(sections),
                ),
                courses or [NormalizedCourse(SUBJECT, COURSE, "Natural Language", 3.0)],
                sections,
                [],
                ValidationReport(True, 1.0, ()),
            )
    finally:
        sync_engine.dispose()


@pytest.mark.asyncio
async def test_lookup_returns_grouped_published_offerings_and_precise_filters():
    provider = f"test-retrieval-{uuid.uuid4()}"
    requested_unit = f"{TERM}:{SUBJECT}"
    old_id = _publish(
        provider,
        requested_unit,
        "https://oscar.gatech.edu/old",
        [_section("99999", "Old Campus", "Old Instructor", (_meeting("M", time(9), time(10)),))],
    )
    fixture_html = (
        Path(__file__).parents[1] / "fixtures" / "oscar_schedule_sample.html"
    ).read_text()
    samples, parse_failures = parse_schedule_listing(fixture_html, max_records=None)
    courses, sections, normalization_failures = normalize_sections(TERM, samples)
    assert parse_failures == []
    assert normalization_failures == []
    sections[0] = replace(
        sections[0],
        meetings=sections[0].meetings + (_meeting("", None, None, tba=True),),
    )
    current_id = _publish(
        provider,
        requested_unit,
        "https://oscar.gatech.edu/current",
        sections,
        courses,
    )
    async_engine = create_async_engine(settings.database_url)

    try:
        sessions = async_sessionmaker(async_engine, expire_on_commit=False)
        async with sessions() as session:
            offerings = await lookup_course_offerings(
                session,
                CourseQuery(TERM, SUBJECT, COURSE),
            )
            assert [offering.crn for offering in offerings] == ["89627", "90427"]
            assert all(offering.data_version_id == current_id for offering in offerings)
            assert all(offering.data_version_id != old_id for offering in offerings)

            online, atlanta = offerings
            assert online.instructors == ("Mark O Riedl",)
            assert online.meetings[0].is_tba is True
            assert atlanta.instructors == ("Kartik Goyal",)
            assert len(atlanta.meetings) == 2
            assert atlanta.meetings[0].start_time == time(15, 30)
            assert atlanta.meetings[0].days == "MW"
            assert atlanta.meetings[1].is_tba is True
            assert atlanta.source_url == "https://oscar.gatech.edu/current"
            assert atlanta.data_as_of.tzinfo is not None
            assert atlanta.freshness is FreshnessState.CURRENT

            filtered = await lookup_course_offerings(
                session,
                CourseQuery(
                    TERM,
                    SUBJECT,
                    COURSE,
                    campus="Georgia Tech-Atlanta * Campus",
                    days=("M",),
                    starts_after=time(15, 30),
                    ends_before=time(16, 45),
                ),
            )
            assert [offering.crn for offering in filtered] == ["90427"]
            assert len(filtered[0].meetings) == 2

            wednesday = await lookup_course_offerings(
                session,
                CourseQuery(TERM, SUBJECT, COURSE, days=("W",)),
            )
            assert [offering.crn for offering in wednesday] == ["90427"]

            assert (
                await lookup_course_offerings(
                    session,
                    CourseQuery(TERM, SUBJECT, "9999"),
                )
                == []
            )
    finally:
        await async_engine.dispose()
        sync_engine = create_engine(settings.database_url_sync)
        try:
            with Session(sync_engine) as cleanup:
                cleanup.execute(delete(DataVersion).where(DataVersion.provider == provider))
                cleanup.commit()
        finally:
            sync_engine.dispose()
