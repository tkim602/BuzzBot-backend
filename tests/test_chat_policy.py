"""Unit tests for chat ambiguity policy helpers."""

from app.api.chat import (
    _attempt_admissions_deadline_rule_answer,
    _augment_citations_with_date_evidence,
    _date_claims_supported,
    _is_ambiguous_admissions_deadline_query,
    _is_ambiguous_registration_deadline_query,
)
from app.rag.retrieval import RetrievedChunk


def test_registration_deadline_query_is_ambiguous_without_event_detail() -> None:
    assert _is_ambiguous_registration_deadline_query(
        "When is the registration deadline?",
        "registrar_calendar",
    )


def test_registration_deadline_query_not_ambiguous_with_specific_event() -> None:
    assert not _is_ambiguous_registration_deadline_query(
        "When is the last day to register or add courses for Spring 2026?",
        "registrar_calendar",
    )


def test_admissions_deadline_query_is_ambiguous_without_program() -> None:
    assert _is_ambiguous_admissions_deadline_query(
        "when is the application deadline for Fall 2026",
        "admissions_deadline",
    )


def test_omscs_admissions_deadline_query_is_ambiguous_without_term() -> None:
    assert _is_ambiguous_admissions_deadline_query(
        "application deadline for OMSCS",
        "admissions_deadline",
    )


def test_admissions_deadline_query_not_ambiguous_for_mscs() -> None:
    assert not _is_ambiguous_admissions_deadline_query(
        "application deadline for MSCS",
        "admissions_deadline",
    )


def test_date_citation_augmentation_adds_supporting_quote() -> None:
    answer = "The deadline is 2026-01-09."
    chunks = [
        RetrievedChunk(
            chunk_id="c1",
            url="https://registrar.gatech.edu/calendar/academic-calendar",
            title="Academic Calendar",
            chunk_text="Spring 2026 Registration Date: 2026-01-09 Last day to register or add courses.",
            score=1.0,
            source_name="gt-calendar-events",
            fetched_at="2026-02-19T00:00:00+00:00",
        )
    ]
    augmented = _augment_citations_with_date_evidence(answer, [], chunks)
    assert len(augmented) == 1
    assert "2026-01-09" in augmented[0]["quote"]
    assert _date_claims_supported(answer, augmented)


def test_rule_based_admissions_fallback_extracts_omscs_fall_deadline() -> None:
    chunks = [
        RetrievedChunk(
            chunk_id="o1",
            url="https://omscs.gatech.edu/deadlines-decisions-requirements-and-guidelines",
            title="OMSCS Deadlines",
            chunk_text=(
                "Application deadline for Fall matriculation: March 1 "
                "Application deadline for Spring matriculation: August 15"
            ),
            score=1.0,
            source_name="gt-omscs",
            fetched_at="2026-02-19T00:00:00+00:00",
        )
    ]
    fallback = _attempt_admissions_deadline_rule_answer(
        "application deadline for OMSCS Fall 2026",
        chunks,
    )
    assert fallback is not None
    raw_answer, citations = fallback
    assert "March 1" in raw_answer["answer"]
    assert (
        citations[0]["url"]
        == "https://omscs.gatech.edu/deadlines-decisions-requirements-and-guidelines"
    )


def test_rule_based_admissions_fallback_extracts_mscs_deadline() -> None:
    chunks = [
        RetrievedChunk(
            chunk_id="m1",
            url="https://catalog.gatech.edu/programs/computer-science-ms",
            title="MSCS",
            chunk_text=(
                "Master of Science in Computer Science (MSCS). "
                "Applicants are selected for fall semester admission only. "
                "The application deadline is February 1."
            ),
            score=1.0,
            source_name="gt-catalog",
            fetched_at="2026-02-19T00:00:00+00:00",
        )
    ]
    fallback = _attempt_admissions_deadline_rule_answer(
        "application deadline for MSCS",
        chunks,
    )
    assert fallback is not None
    raw_answer, citations = fallback
    assert "February 1" in raw_answer["answer"]
    assert citations[0]["url"] == "https://catalog.gatech.edu/programs/computer-science-ms"
