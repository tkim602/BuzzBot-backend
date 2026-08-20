from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.retrieval.documents import DocumentEvidence
from app.retrieval.tools import (
    CourseDetailsQuery,
    RegistrationCalendarQuery,
    lookup_course_details,
    lookup_registration_calendar,
)


def test_tool_queries_validate_and_normalize_inputs():
    assert CourseDetailsQuery(" cs ", "7650") == CourseDetailsQuery("CS", "7650")
    with pytest.raises(ValueError, match="subject"):
        CourseDetailsQuery("", "7650")
    with pytest.raises(ValueError, match="course_number"):
        CourseDetailsQuery("CS", "")
    with pytest.raises(ValueError, match="text"):
        RegistrationCalendarQuery("  ")
    with pytest.raises(ValueError, match="top_k"):
        CourseDetailsQuery("CS", "7650", top_k=21)


@pytest.mark.asyncio
async def test_course_details_is_pinned_to_catalog_authority(monkeypatch):
    evidence = _evidence("course_catalog", "catalog")
    search = AsyncMock(return_value=[evidence])
    monkeypatch.setattr("app.retrieval.tools.search_policy_docs", search)

    result = await lookup_course_details(object(), CourseDetailsQuery("cs", "7650"), [0.1])

    assert result == [evidence]
    policy_query = search.await_args.args[1]
    assert policy_query.text == "CS 7650 course description credits prerequisites"
    assert policy_query.source_types == ("course_catalog",)


@pytest.mark.asyncio
async def test_registration_calendar_is_pinned_to_calendar_authority(monkeypatch):
    evidence = _evidence("academic_calendar", "academic_calendar")
    search = AsyncMock(return_value=[evidence])
    monkeypatch.setattr("app.retrieval.tools.search_policy_docs", search)

    result = await lookup_registration_calendar(
        object(),
        RegistrationCalendarQuery("When is Fall 2026 registration?"),
        [0.2],
    )

    assert result == [evidence]
    policy_query = search.await_args.args[1]
    assert policy_query.text == "When is Fall 2026 registration?"
    assert policy_query.source_types == ("academic_calendar",)


def _evidence(source_type: str, authority: str) -> DocumentEvidence:
    return DocumentEvidence(
        chunk_id="chunk-1",
        text="Official GT evidence",
        title="Official page",
        canonical_url="https://example.gatech.edu/official",
        source_name="gt-official",
        source_type=source_type,
        authority=authority,
        fetched_at="2026-08-20T00:00:00+00:00",
        edition=None,
        score=0.9,
        retrieval_method="hybrid_rrf",
    )
