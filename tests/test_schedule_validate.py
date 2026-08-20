from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest

from ingestion.schedule.types import (
    NormalizedCourse,
    NormalizedMeeting,
    NormalizedSection,
    ParseFailure,
)
from ingestion.schedule.validate import (
    CollectionPlan,
    FreshnessState,
    freshness_state,
    validate_collection,
)

NOW = datetime.now(UTC)


def _collection():
    course = NormalizedCourse("CS", "7650", "Natural Language", 3.0)
    meeting = NormalizedMeeting(
        meeting_type="Class",
        days="MW",
        start_time=time(15, 30),
        end_time=time(16, 45),
        building="Paper Tricentennial",
        room="109",
        start_date=date(2026, 8, 24),
        end_date=date(2026, 12, 17),
        is_tba=False,
    )
    section = NormalizedSection(
        term_code="202608",
        term_name="Fall 2026",
        crn="90427",
        course_key=("CS", "7650"),
        section_code="A",
        campus="Georgia Tech-Atlanta * Campus",
        schedule_type="Lecture",
        instructors=("Kartik Goyal",),
        meetings=(meeting,),
    )
    plan = CollectionPlan(
        term_code="202608",
        planned_subjects=("CS",),
        completed_subjects=("CS",),
        failed_units=(),
        records_fetched=1,
        records_parsed=1,
    )
    return plan, [course], [section]


def _codes(report):
    return {issue.code for issue in report.issues}


def test_partial_subject_collection_is_invalid():
    plan, courses, sections = _collection()
    plan = replace(plan, completed_subjects=())

    report = validate_collection(plan, courses, sections, [], NOW)

    assert report.valid is False
    assert "COLLECTION_INCOMPLETE" in _codes(report)


def test_normalized_subjects_must_include_every_planned_subject():
    plan, courses, sections = _collection()
    plan = replace(
        plan,
        planned_subjects=("CS", "MATH"),
        completed_subjects=("CS", "MATH"),
    )

    report = validate_collection(plan, courses, sections, [], NOW)

    assert "SUBJECT_COVERAGE_MISMATCH" in _codes(report)


def test_normalized_subjects_must_not_include_unplanned_subjects():
    plan, courses, sections = _collection()
    math_course = replace(courses[0], subject="MATH")
    math_section = replace(sections[0], course_key=("MATH", "7650"))

    report = validate_collection(plan, [math_course], [math_section], [], NOW)

    assert "SUBJECT_COVERAGE_MISMATCH" in _codes(report)


def test_parse_success_below_99_percent_is_invalid():
    plan, courses, sections = _collection()
    plan = replace(plan, records_fetched=2, records_parsed=1)
    failures = [
        ParseFailure("SECTION_HEADER_INVALID", "bad-1", "missing header"),
    ]

    report = validate_collection(plan, courses, sections, failures, NOW)

    assert report.parse_success_rate == pytest.approx(0.5)
    assert "PARSE_RATE_LOW" in _codes(report)


def test_duplicate_crn_is_invalid_within_the_exact_term():
    plan, courses, sections = _collection()
    duplicate = replace(sections[0], course_key=("CS", "7650"), section_code="B")

    report = validate_collection(plan, courses, [*sections, duplicate], [], NOW)

    assert "DUPLICATE_CRN" in _codes(report)


def test_section_must_reference_an_exact_course_key():
    plan, courses, sections = _collection()
    sections = [replace(sections[0], course_key=("CS7", "650"))]

    report = validate_collection(plan, courses, sections, [], NOW)

    assert "COURSE_REFERENCE_MISSING" in _codes(report)


def test_empty_collection_is_invalid_without_dividing_by_zero():
    plan, _, _ = _collection()
    plan = replace(plan, records_fetched=0, records_parsed=0)

    report = validate_collection(plan, [], [], [], NOW)

    assert report.parse_success_rate == 0.0
    assert "EMPTY_COLLECTION" in _codes(report)


def test_verified_empty_subject_is_a_valid_zero_row_collection():
    plan, _, _ = _collection()
    plan = replace(
        plan,
        records_fetched=0,
        records_parsed=0,
        verified_empty_subjects=("CS",),
    )

    report = validate_collection(plan, [], [], [], NOW)

    assert report.valid is True
    assert report.parse_success_rate == 0.0


def test_verified_empty_subject_must_belong_to_the_completed_plan():
    plan, _, _ = _collection()
    plan = replace(
        plan,
        records_fetched=0,
        records_parsed=0,
        verified_empty_subjects=("MATH",),
    )

    report = validate_collection(plan, [], [], [], NOW)

    assert report.valid is False
    assert "VERIFIED_EMPTY_INVALID" in _codes(report)


def test_invalid_parse_denominator_is_reported():
    plan, courses, sections = _collection()
    plan = replace(plan, records_fetched=1, records_parsed=2)

    report = validate_collection(plan, courses, sections, [], NOW)

    assert report.parse_success_rate == 0.0
    assert "RECORD_COUNTS_INVALID" in _codes(report)


@pytest.mark.parametrize(
    ("records_fetched", "records_parsed", "section_count"),
    [(2, 2, 1), (1, 1, 2)],
)
def test_declared_parsed_count_must_match_normalized_sections(
    records_fetched, records_parsed, section_count
):
    plan, courses, sections = _collection()
    plan = replace(plan, records_fetched=records_fetched, records_parsed=records_parsed)
    if section_count == 2:
        sections.append(replace(sections[0], crn="90428", section_code="B"))

    report = validate_collection(plan, courses, sections, [], NOW)

    assert "RECORD_COUNTS_INVALID" in _codes(report)


@pytest.mark.parametrize("field", ["subject", "course_number", "title"])
def test_required_course_fields_must_be_non_empty(field):
    plan, courses, sections = _collection()
    courses = [replace(courses[0], **{field: ""})]

    report = validate_collection(plan, courses, sections, [], NOW)

    assert "COURSE_FIELD_MISSING" in _codes(report)


@pytest.mark.parametrize(
    "field",
    ["term_code", "term_name", "crn", "section_code", "campus", "schedule_type"],
)
def test_required_section_fields_must_be_non_empty(field):
    plan, courses, sections = _collection()
    sections = [replace(sections[0], **{field: ""})]

    report = validate_collection(plan, courses, sections, [], NOW)

    assert "SECTION_FIELD_MISSING" in _codes(report)


@pytest.mark.parametrize("field", ["meeting_type", "days"])
def test_required_timed_meeting_fields_must_be_non_empty(field):
    plan, courses, sections = _collection()
    meeting = replace(sections[0].meetings[0], **{field: ""})
    sections = [replace(sections[0], meetings=(meeting,))]

    report = validate_collection(plan, courses, sections, [], NOW)

    assert "MEETING_FIELD_MISSING" in _codes(report)


def test_source_arranged_section_without_meeting_rows_is_valid():
    plan, courses, sections = _collection()
    missing = replace(sections[0], meetings=())

    report = validate_collection(plan, courses, [missing], [], NOW)

    assert report.valid is True


def test_explicit_tba_meeting_must_not_have_times():
    plan, courses, sections = _collection()
    inconsistent_tba = replace(
        sections[0].meetings[0],
        is_tba=True,
        days="",
        building=None,
        room=None,
    )

    inconsistent_report = validate_collection(
        plan,
        courses,
        [replace(sections[0], meetings=(inconsistent_tba,))],
        [],
        NOW,
    )

    assert "MEETING_INCOMPLETE" in _codes(inconsistent_report)


def test_explicit_tba_meeting_is_complete():
    plan, courses, sections = _collection()
    tba = replace(
        sections[0].meetings[0],
        days="",
        start_time=None,
        end_time=None,
        building=None,
        room=None,
        is_tba=True,
    )

    report = validate_collection(plan, courses, [replace(sections[0], meetings=(tba,))], [], NOW)

    assert report.valid is True


def test_tba_meeting_rejects_a_partial_time_range():
    plan, courses, sections = _collection()
    partial_tba = replace(
        sections[0].meetings[0], start_time=None, end_time=time(16, 45), is_tba=True
    )

    report = validate_collection(
        plan, courses, [replace(sections[0], meetings=(partial_tba,))], [], NOW
    )

    assert "MEETING_INCOMPLETE" in _codes(report)


def test_future_fetched_at_is_invalid():
    plan, courses, sections = _collection()

    report = validate_collection(plan, courses, sections, [], NOW + timedelta(minutes=1))

    assert "FETCHED_AT_FUTURE" in _codes(report)


def test_freshness_transitions_include_boundary_values():
    assert freshness_state(NOW - timedelta(hours=6), NOW) is FreshnessState.CURRENT
    assert freshness_state(NOW - timedelta(hours=7), NOW) is FreshnessState.STALE
    assert freshness_state(NOW - timedelta(hours=24), NOW) is FreshnessState.STALE
    assert freshness_state(NOW - timedelta(hours=25), NOW) is FreshnessState.EXPIRED


def test_historical_data_does_not_expire_but_remains_stale():
    fetched_at = NOW - timedelta(days=365)

    assert freshness_state(fetched_at, NOW, historical=True) is FreshnessState.STALE
    assert freshness_state(fetched_at, NOW) is FreshnessState.EXPIRED


def test_freshness_requires_aware_timestamps_and_rejects_the_future():
    eastern = timezone(timedelta(hours=-4))

    assert freshness_state(NOW.astimezone(eastern), NOW) is FreshnessState.CURRENT
    with pytest.raises(ValueError, match="timezone-aware"):
        freshness_state(NOW.replace(tzinfo=None), NOW)
    with pytest.raises(ValueError, match="future"):
        freshness_state(NOW + timedelta(seconds=1), NOW)
