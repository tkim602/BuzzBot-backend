from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from db.models import AcademicTerm, Course, DataVersion, Meeting, Section

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="set RUN_DB_TESTS=1 for PostgreSQL tests"
)


@pytest.fixture
def connection():
    engine = create_engine(settings.database_url_sync)
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()
    engine.dispose()


def _versioned_parents(connection: Connection):
    version_1, version_2 = uuid.uuid4(), uuid.uuid4()
    term_1, term_2 = uuid.uuid4(), uuid.uuid4()
    course_1, course_2 = uuid.uuid4(), uuid.uuid4()
    connection.execute(
        insert(DataVersion),
        [
            {
                "id": version_1,
                "provider": "test",
                "requested_unit": "v1",
                "status": "STAGED",
            },
            {
                "id": version_2,
                "provider": "test",
                "requested_unit": "v2",
                "status": "STAGED",
            },
        ],
    )
    connection.execute(
        insert(AcademicTerm),
        [
            {
                "id": term_1,
                "data_version_id": version_1,
                "term_code": "202608",
                "display_name": "Fall 2026",
            },
            {
                "id": term_2,
                "data_version_id": version_2,
                "term_code": "202608",
                "display_name": "Fall 2026",
            },
        ],
    )
    connection.execute(
        insert(Course),
        [
            {
                "id": course_1,
                "data_version_id": version_1,
                "subject": "CS",
                "course_number": "7650",
                "title": "Natural Language",
                "credits": 3.0,
            },
            {
                "id": course_2,
                "data_version_id": version_2,
                "subject": "CS",
                "course_number": "7650",
                "title": "Natural Language",
                "credits": 3.0,
            },
        ],
    )
    return version_1, version_2, term_1, term_2, course_1, course_2


def test_postgres_rejects_cross_version_section(connection: Connection):
    version_1, _, _, term_2, _, course_2 = _versioned_parents(connection)

    with pytest.raises(IntegrityError):
        connection.execute(
            insert(Section),
            {
                "id": uuid.uuid4(),
                "data_version_id": version_1,
                "academic_term_id": term_2,
                "course_id": course_2,
                "term_code": "202608",
                "crn": "12345",
                "section_code": "A",
                "campus": "Georgia Tech-Atlanta",
                "schedule_type": "Lecture",
            },
        )


def test_postgres_rejects_cross_version_meeting(connection: Connection):
    version_1, version_2, _, term_2, _, course_2 = _versioned_parents(connection)
    section_id = uuid.uuid4()
    connection.execute(
        insert(Section),
        {
            "id": section_id,
            "data_version_id": version_2,
            "academic_term_id": term_2,
            "course_id": course_2,
            "term_code": "202608",
            "crn": "12345",
            "section_code": "A",
            "campus": "Georgia Tech-Atlanta",
            "schedule_type": "Lecture",
        },
    )

    with pytest.raises(IntegrityError):
        connection.execute(
            insert(Meeting),
            {
                "id": uuid.uuid4(),
                "data_version_id": version_1,
                "section_id": section_id,
                "meeting_type": "Class",
                "start_date": "2026-08-24",
                "end_date": "2026-12-17",
            },
        )
