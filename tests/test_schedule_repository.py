from __future__ import annotations

from datetime import UTC, date, datetime, time
from unittest.mock import MagicMock

from sqlalchemy.sql.dml import Insert, Update

from ingestion.schedule.repository import SafeSnapshot, publish_collection
from ingestion.schedule.types import (
    NormalizedCourse,
    NormalizedMeeting,
    NormalizedSection,
    ParseFailure,
)
from ingestion.schedule.validate import (
    CollectionPlan,
    ValidationIssue,
    ValidationReport,
)


def _snapshot() -> SafeSnapshot:
    return SafeSnapshot(
        source_url="https://oscar.gatech.edu/schedule",
        fetched_at=datetime(2026, 8, 17, tzinfo=UTC),
        status_code=200,
        content_type="text/html",
        content_hash="a" * 64,
        parser_version="oscar-v1",
        raw_location="artifacts/oscar/sample.html",
    )


def _plan() -> CollectionPlan:
    return CollectionPlan("202608", ("CS",), ("CS",), (), 1, 1)


def _records():
    course = NormalizedCourse("CS", "7650", "Natural Language", 3.0)
    meeting = NormalizedMeeting(
        meeting_type="Class",
        days="TR",
        start_time=time(15, 30),
        end_time=time(16, 45),
        building="Klaus",
        room="1447",
        start_date=date(2026, 8, 24),
        end_date=date(2026, 12, 17),
        is_tba=False,
    )
    section = NormalizedSection(
        term_code="202608",
        term_name="Fall 2026",
        crn="12345",
        course_key=("CS", "7650"),
        section_code="A",
        campus="Georgia Tech-Atlanta Campus",
        schedule_type="Lecture",
        instructors=("Ada Lovelace",),
        meetings=(meeting,),
    )
    return [course], [section]


def _inserted_values(session: MagicMock) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = {}
    for call in session.execute.call_args_list:
        statement = call.args[0]
        if not isinstance(statement, Insert):
            continue
        values = call.args[1]
        rows[statement.table.name] = values if isinstance(values, list) else [values]
    return rows


def test_invalid_report_records_failed_version_without_superseding():
    session = MagicMock()
    report = ValidationReport(
        valid=False,
        parse_success_rate=0.0,
        issues=(ValidationIssue("EMPTY_COLLECTION", None, "no usable records"),),
    )

    version_id = publish_collection(
        session,
        "public-oscar",
        "202608:CS",
        _snapshot(),
        CollectionPlan("202608", ("CS",), (), ("CS",), 1, 0),
        [],
        [],
        [ParseFailure("SECTION_INVALID", "12345", "bad section")],
        report,
    )

    inserted = _inserted_values(session)
    assert inserted["data_versions"][0]["id"] == version_id
    assert inserted["data_versions"][0]["status"] == "FAILED"
    assert inserted["source_snapshots"][0]["validation_status"] == "FAILED"
    assert {row["error_code"] for row in inserted["ingestion_errors"]} == {
        "EMPTY_COLLECTION",
        "SECTION_INVALID",
    }
    assert not any(isinstance(call.args[0], Update) for call in session.execute.call_args_list)


def test_valid_report_maps_all_rows_to_the_same_version():
    session = MagicMock()
    session.scalars.return_value.first.return_value = None
    courses, sections = _records()

    version_id = publish_collection(
        session,
        "public-oscar",
        "202608:CS",
        _snapshot(),
        _plan(),
        courses,
        sections,
        [],
        ValidationReport(True, 1.0, ()),
    )

    inserted = _inserted_values(session)
    assert inserted["data_versions"][0]["id"] == version_id
    for table in (
        "academic_terms",
        "courses",
        "sections",
        "meetings",
        "source_snapshots",
    ):
        assert {row["data_version_id"] for row in inserted[table]} == {version_id}

    term = inserted["academic_terms"][0]
    course = inserted["courses"][0]
    section = inserted["sections"][0]
    meeting = inserted["meetings"][0]
    assert section["academic_term_id"] == term["id"]
    assert section["course_id"] == course["id"]
    assert meeting["section_id"] == section["id"]
    assert section["instructors_json"] == ["Ada Lovelace"]
    assert meeting["start_time"] == time(15, 30)
