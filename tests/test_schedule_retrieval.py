from __future__ import annotations

from datetime import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.schedule import CourseQuery, lookup_course_offerings


@pytest.mark.asyncio
async def test_lookup_builds_bound_exact_query_with_same_meeting_filters():
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.mappings.return_value.all.return_value = []
    session.execute.return_value = result

    offerings = await lookup_course_offerings(
        session,
        CourseQuery(
            term_code="202608",
            subject="CS",
            course_number="7650",
            campus="Georgia Tech-Atlanta Campus",
            days=("T", "R"),
            starts_after=time(15, 30),
            ends_before=time(16, 45),
        ),
    )

    assert offerings == []
    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "data_versions.status =" in sql
    assert "courses.subject =" in sql
    assert "courses.course_number =" in sql
    assert "sections.term_code =" in sql
    assert "sections.campus =" in sql
    assert "EXISTS" in sql
    assert "meetings_1.start_time >=" in sql
    assert "meetings_1.end_time <=" in sql
    assert set(compiled.params.values()) >= {
        "PUBLISHED",
        "202608",
        "CS",
        "7650",
        "Georgia Tech-Atlanta Campus",
        "T",
        "R",
        time(15, 30),
        time(16, 45),
    }


@pytest.mark.parametrize("days", [("X",), ("M", "M")])
def test_course_query_rejects_invalid_or_duplicate_days(days: tuple[str, ...]):
    with pytest.raises(ValueError, match="days"):
        CourseQuery("202608", "CS", "7650", days=days)


def test_course_query_rejects_an_inverted_time_window():
    with pytest.raises(ValueError, match="starts_after"):
        CourseQuery(
            "202608",
            "CS",
            "7650",
            starts_after=time(17),
            ends_before=time(16),
        )
