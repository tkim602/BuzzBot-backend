from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.graph.workflow import WorkflowServices, _policy_source_types, build_workflow
from app.retrieval.documents import DocumentEvidence
from ingestion.schedule.validate import FreshnessState


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("How do financial-aid appeals work?", ("finance",)),
        ("How do I cancel my housing contract?", ("housing", "dining")),
        ("How do I request disability accommodations?", ("health_support",)),
        ("What counts as full-time enrollment for F-1 students?", ("international",)),
        ("Does the Stinger bus require a fare?", ("campus_operations",)),
        ("Who is eligible to become a Knack tutor?", ("student_support",)),
        (
            "What is the undergraduate minor credit-hour requirement?",
            (
                "official_policy",
                "academic_calendar",
                "course_catalog",
                "omscs_policy",
                "degree_programs",
                "academic_policy",
                "academic_lifecycle",
            ),
        ),
        ("What are first-year recommendation requirements?", ("admissions",)),
    ],
)
def test_policy_source_routing_uses_domain_verticals(query, expected):
    assert _policy_source_types(query) == expected


def test_policy_source_routing_preserves_cross_domain_precedence():
    assert _policy_source_types("Does OMSCS offer financial aid?") == ("omscs_policy",)
    assert _policy_source_types("Are first-year meal plans required?") == ("housing", "dining")
    assert _policy_source_types("How do I request disability housing accommodations?") == (
        "health_support",
    )
    assert _policy_source_types("What is the minimum satisfactory GPA?") != ("admissions",)


@pytest.mark.asyncio
async def test_schedule_path_is_deterministic_and_cited(monkeypatch):
    offering = SimpleNamespace(
        term_code="202608",
        subject="CS",
        course_number="7650",
        title="Natural Language",
        credits=3.0,
        crn="12345",
        section_code="A",
        campus="Atlanta",
        schedule_type="Lecture",
        instructional_method="In Person",
        instructors=("Ada Lovelace",),
        notes=None,
        meetings=(),
        source_url="https://oscar.gatech.edu/bprod/bwckschd.p_disp_detail_sched",
        data_as_of=datetime(2026, 8, 20, tzinfo=UTC),
        data_version_id=uuid.uuid4(),
        freshness=FreshnessState.CURRENT,
    )
    lookup = AsyncMock(return_value=[offering])
    monkeypatch.setattr("app.graph.workflow.lookup_course_offerings", lookup)
    embed = AsyncMock(side_effect=AssertionError("schedule SQL must not embed"))
    answer = AsyncMock(side_effect=AssertionError("schedule answer must not call an LLM"))
    graph = build_workflow(WorkflowServices(object(), embed, answer))

    result = await graph.ainvoke({"query": "Is CS 7650 offered in Fall 2026?"})

    assert "CRN 12345" in result["answer"]
    assert result["citations"][0]["url"].startswith("https://oscar.gatech.edu/")
    assert result["answer_valid"] is True
    assert result["retry_count"] == 0
    embed.assert_not_awaited()
    answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_schedule_term_clarifies_without_retrieval(monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr("app.graph.workflow.lookup_course_offerings", lookup)
    graph = build_workflow(WorkflowServices(object(), AsyncMock(), AsyncMock()))

    result = await graph.ainvoke({"query": "What sections does CS 7650 have?"})

    assert "term" in result["answer"].lower()
    assert result["citations"] == []
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_document_evidence_retries_once_then_abstains(monkeypatch):
    search = AsyncMock(return_value=[])
    monkeypatch.setattr("app.graph.workflow.search_policy_docs", search)
    embed = AsyncMock(return_value=[0.1])
    graph = build_workflow(WorkflowServices(object(), embed, AsyncMock()))

    result = await graph.ainvoke({"query": "What documents are required for OMSCS admission?"})

    assert result["retry_count"] == 1
    assert search.await_count == 2
    assert embed.await_count == 2
    assert result["citations"] == []
    assert result["confidence"] == 0.2
    assert "enough official evidence" in result["answer"].lower()


@pytest.mark.asyncio
async def test_invalid_answer_citation_abstains_without_an_open_loop(monkeypatch):
    evidence = _document_evidence()
    monkeypatch.setattr("app.graph.workflow.search_policy_docs", AsyncMock(return_value=[evidence]))
    answer = AsyncMock(
        return_value={
            "answer": "Unsupported answer",
            "citations": [{"url": "https://evil.example/", "title": "Wrong", "quote": "invented"}],
            "confidence": 0.9,
            "notes": [],
        }
    )
    graph = build_workflow(WorkflowServices(object(), AsyncMock(return_value=[0.1]), answer))

    result = await graph.ainvoke({"query": "What documents are required for OMSCS admission?"})

    assert result["answer_valid"] is False
    assert result["citations"] == []
    assert result["confidence"] == 0.2
    assert answer.await_count == 1


@pytest.mark.asyncio
async def test_grounded_document_answer_keeps_official_citation(monkeypatch):
    evidence = _document_evidence()
    monkeypatch.setattr("app.graph.workflow.search_policy_docs", AsyncMock(return_value=[evidence]))
    answer = AsyncMock(
        return_value={
            "answer": "Use the official admissions checklist.",
            "citations": [
                {
                    "url": evidence.canonical_url,
                    "title": evidence.title,
                    "quote": "Official admissions checklist",
                }
            ],
            "confidence": 0.8,
            "notes": [],
        }
    )
    graph = build_workflow(WorkflowServices(object(), AsyncMock(return_value=[0.1]), answer))

    result = await graph.ainvoke({"query": "What documents are required for OMSCS admission?"})

    assert result["answer_valid"] is True
    assert result["citations"][0]["url"] == evidence.canonical_url
    assert result["confidence"] == 0.8


@pytest.mark.asyncio
async def test_policy_answer_with_grounded_quote_but_contradictory_claim_abstains(monkeypatch):
    evidence = DocumentEvidence(
        chunk_id="major-selection",
        text=(
            "When you apply to Georgia Tech as a first-year applicant, "
            "you do not apply to a specific major or college."
        ),
        title="Major Selection in the Application Process",
        canonical_url="https://admission.gatech.edu/first-year/major-selection",
        source_name="gt-admission",
        source_type="admissions",
        authority="admissions",
        fetched_at="2026-08-20T00:00:00+00:00",
        edition=None,
        score=0.9,
        retrieval_method="hybrid_rrf",
    )
    search = AsyncMock(return_value=[evidence])
    monkeypatch.setattr("app.graph.workflow.search_policy_docs", search)
    answer = AsyncMock(
        return_value={
            "answer": "First-year applicants apply directly to a specific major or college.",
            "citations": [
                {
                    "url": evidence.canonical_url,
                    "title": evidence.title,
                    "quote": "you do not apply to a specific major or college",
                }
            ],
            "confidence": 0.9,
            "notes": [],
        }
    )
    graph = build_workflow(WorkflowServices(object(), AsyncMock(return_value=[0.1]), answer))

    result = await graph.ainvoke(
        {"query": "Do first-year applicants apply directly to a specific major or college?"}
    )

    assert result["answer_valid"] is False
    assert result["citations"] == []
    assert "enough official evidence" in result["answer"].lower()
    assert search.await_args.args[1].source_types == ("admissions",)


@pytest.mark.asyncio
async def test_yes_no_answer_with_wrong_leading_polarity_abstains(monkeypatch):
    evidence = DocumentEvidence(
        chunk_id="deadlines",
        text=(
            "Early Action 1 — Application Deadline: October 15\n"
            "Early Action 2 — Application Deadline: November 2"
        ),
        title="First-Year Deadlines",
        canonical_url="https://admission.gatech.edu/first-year/deadlines",
        source_name="gt-admission",
        source_type="admissions",
        authority="admissions",
        fetched_at="2026-08-20T00:00:00+00:00",
        edition=None,
        score=0.9,
        retrieval_method="hybrid_rrf",
    )
    monkeypatch.setattr("app.graph.workflow.search_policy_docs", AsyncMock(return_value=[evidence]))
    answer = AsyncMock(
        return_value={
            "answer": "Yes, November 2 is the Early Action 2 deadline.",
            "citations": [
                {
                    "url": evidence.canonical_url,
                    "title": evidence.title,
                    "quote": "Early Action 2 — Application Deadline: November 2",
                }
            ],
            "confidence": 0.9,
            "notes": [],
            "_binary_verdict": "FALSE",
        }
    )
    graph = build_workflow(WorkflowServices(object(), AsyncMock(return_value=[0.1]), answer))

    result = await graph.ainvoke(
        {"query": "Is November 2 the Early Action 1 application deadline?"}
    )

    assert result["answer_valid"] is False
    assert result["citations"] == []
    assert "enough official evidence" in result["answer"].lower()


def _document_evidence() -> DocumentEvidence:
    return DocumentEvidence(
        chunk_id="chunk-1",
        text="Official admissions checklist and application requirements.",
        title="OMSCS Admissions",
        canonical_url="https://omscs.gatech.edu/admission-criteria",
        source_name="gt-omscs",
        source_type="omscs_policy",
        authority="omscs",
        fetched_at="2026-08-20T00:00:00+00:00",
        edition=None,
        score=0.9,
        retrieval_method="hybrid_rrf",
    )


@pytest.mark.asyncio
async def test_checkpointed_thread_clears_optional_fields_between_queries(monkeypatch):
    monkeypatch.setattr("app.graph.workflow.search_policy_docs", AsyncMock(return_value=[]))
    graph = build_workflow(
        WorkflowServices(object(), AsyncMock(return_value=[0.1]), AsyncMock()),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "thread-1"}}

    first = await graph.ainvoke({"query": "What sections does CS 7650 have?"}, config)
    second = await graph.ainvoke({"query": "What documents are required for OMSCS?"}, config)

    assert first["subject"] == "CS"
    assert second["subject"] is None
    assert second["course_number"] is None
    assert second["term_code"] is None
