from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.rag import retrieval
from app.rag.answerer import generate_answer
from app.rag.grounding import check_claim_support
from app.rag.retrieval import RetrievedChunk, _lexical_match_score
from app.rag.router import classify_query
from ingestion.chunk import chunk_text

OMSCS_QUERY = (
    "How many credits and courses are required for the OMSCS degree, "
    "and what GPA is required to graduate?"
)
DEADLINES_QUERY = "What are Georgia Tech first-year application deadlines?"
RECOMMENDATIONS_QUERY = "Are recommendations required for first-year applicants, and how many?"
MAJOR_QUERY = "Do first-year applicants apply directly to a specific major or college?"


def _chunk(chunk_id: str, title: str, text: str, headings: str = "") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        url=f"https://example.gatech.edu/{chunk_id}",
        title=title,
        chunk_text=text,
        headings=headings,
        source_name="gt-admission",
        score=0.1,
    )


def test_omscs_degree_requirements_query_prefers_exact_policy_page():
    degree = _chunk(
        "degree-requirements",
        "Degree Requirements | OMSCS",
        "The OMSCS degree requires 30 total credit hours (10 courses) and a cumulative GPA of 3.0.",
        "Degree Requirements",
    )
    admission = _chunk(
        "admission-criteria",
        "OMSCS Admission Criteria",
        "Applicants need academic preparation in computer science.",
    )

    route = classify_query(OMSCS_QUERY)

    assert route.intent == "policy"
    assert route.source_filter == "gt-omscs"
    assert _lexical_match_score(OMSCS_QUERY, degree) > _lexical_match_score(OMSCS_QUERY, admission)


def test_first_year_deadline_dates_survive_chunking():
    source = """## First-Year Application Plans and Deadlines
{}
## Early Action 1
October 15
November 2
## Regular Decision
January 6
""".format(" ".join(["application information"] * 70))

    chunks = chunk_text(source, chunk_size=100, chunk_overlap=20, min_chunk_size=50)
    indexed = "\n".join(chunk.text for chunk in chunks)
    route = classify_query(DEADLINES_QUERY)

    assert route.source_filter == "gt-admission"
    assert all(date in indexed for date in ("October 15", "November 2", "January 6"))


def test_first_year_recommendation_query_prefers_recommendations_page():
    recommendations = _chunk(
        "recommendations",
        "Recommendations | Undergraduate Admission",
        "Recommendations are completely optional. We accept up to three recommendations.",
        "Counselor Recommendation\nTeacher Recommendation",
    )
    preparation = _chunk(
        "academic-preparation",
        "Academic Preparation | Undergraduate Admission",
        "We review course rigor and academic performance.",
    )

    route = classify_query(RECOMMENDATIONS_QUERY)
    supported, _ = check_claim_support(
        "Recommendations are optional, and applicants may submit up to three recommendations.",
        [recommendations],
    )

    assert route.intent == "policy"
    assert route.source_filter == "gt-admission"
    assert _lexical_match_score(RECOMMENDATIONS_QUERY, recommendations) > _lexical_match_score(
        RECOMMENDATIONS_QUERY, preparation
    )
    assert supported


@pytest.mark.asyncio
async def test_major_selection_contradiction_is_rejected_and_policy_uses_temperature_zero(
    monkeypatch,
):
    major = _chunk(
        "major-selection",
        "Major Selection in the Application Process",
        "When you apply as a first-year applicant, you do not apply to a specific major or college.",
        "Major Selection",
    )
    preparation = _chunk(
        "academic-preparation",
        "Academic Preparation | Undergraduate Admission",
        "We consider how well you are prepared for your intended major.",
    )
    route = classify_query(MAJOR_QUERY)
    supported, notes = check_claim_support(
        "First-year applicants apply directly to a specific major or college.",
        [major],
    )
    call = AsyncMock(return_value='{"answer":"abstain","citations":[],"confidence":0.2,"notes":[]}')
    monkeypatch.setattr("app.rag.answerer._call_llm", call)
    await generate_answer(MAJOR_QUERY, [major], intent="policy")

    assert route.intent == "policy"
    assert route.source_filter == "gt-admission"
    assert _lexical_match_score(MAJOR_QUERY, major) > _lexical_match_score(MAJOR_QUERY, preparation)
    assert not supported
    assert notes
    assert call.await_args.kwargs["temperature"] == 0.0


def test_cross_encoder_receives_title_headings_and_chunk_text(monkeypatch):
    chunk = _chunk(
        "major-selection",
        "Major Selection",
        "You do not apply to a specific major.",
        "Application Process",
    )

    class FakeModel:
        def predict(self, pairs):
            assert pairs == [
                [
                    MAJOR_QUERY,
                    "Major Selection\nApplication Process\nYou do not apply to a specific major.",
                ]
            ]
            return [1.0]

    monkeypatch.setattr(retrieval, "_cross_encoder_model", FakeModel())
    monkeypatch.setattr(retrieval, "_cross_encoder_model_name", settings.rag_rerank_model)

    assert retrieval.rerank_with_cross_encoder(MAJOR_QUERY, [chunk])[0] is chunk
