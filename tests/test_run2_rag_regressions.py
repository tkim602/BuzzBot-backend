from unittest.mock import AsyncMock, Mock

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


@pytest.mark.asyncio
async def test_first_year_recommendation_query_prefers_recommendations_page():
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
    supported, _ = await check_claim_support(
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
    supported, notes = await check_claim_support(
        "First-year applicants apply directly to a specific major or college.",
        [major],
    )
    call = AsyncMock(
        side_effect=[
            "First-year applicants apply directly to a specific major or college.",
            "CONTRADICTED",
            '{"answer":"abstain","citations":[],"confidence":0.2,"notes":[]}',
        ]
    )
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "evidence", "proposition", "semantic_verdict", "binary_verdict", "expected_answer"),
    [
        (
            "Do I have to submit recommendation letters?",
            "Students have the option to send recommendations. This is completely optional.",
            "Recommendation letters are required.",
            "CONTRADICTED",
            "FALSE",
            "No, recommendation letters are optional.",
        ),
        (
            "Is completing nine courses enough to graduate?",
            "The OMSCS degree requires 30 total credit hours (10 courses).",
            "Nine courses are enough to graduate.",
            "CONTRADICTED",
            "FALSE",
            "No, 10 courses are required.",
        ),
        (
            "Is November 2 the Early Action 1 deadline?",
            "Early Action 1 — Application Deadline: October 15\n"
            "Early Action 2 — Application Deadline: November 2",
            "November 2 is the Early Action 1 deadline.",
            "CONTRADICTED",
            "FALSE",
            "No, Early Action 1 is October 15; November 2 is Early Action 2.",
        ),
        (
            "Is November 2 the Early Action 2 deadline?",
            "Early Action 2 — Application Deadline: November 2.",
            "November 2 is the Early Action 2 deadline.",
            "SUPPORTED",
            "TRUE",
            "Yes, November 2 is the Early Action 2 deadline.",
        ),
    ],
)
async def test_factual_yes_no_verdict_constrains_generation(
    monkeypatch, query, evidence, proposition, semantic_verdict, binary_verdict, expected_answer
):
    explanation = expected_answer.split(maxsplit=1)[1]
    response = f'{{"answer":"{explanation}","citations":[],"confidence":1.0,"notes":[]}}'
    call = AsyncMock(side_effect=[proposition, semantic_verdict, response])
    monkeypatch.setattr("app.rag.answerer._call_llm", call)

    answer = await generate_answer(query, [_chunk("policy", "Policy", evidence)], intent="policy")

    proposition_system, proposition_user = call.await_args_list[0].args
    semantic_system, semantic_user = call.await_args_list[1].args
    answer_system, answer_user = call.await_args_list[2].args
    expected_polarity = "Yes" if binary_verdict == "TRUE" else "No"
    assert answer["answer"] == expected_answer
    assert answer["_binary_verdict"] == binary_verdict
    assert "atomic factual proposition" in proposition_system
    assert query in proposition_user
    assert proposition in semantic_user
    assert evidence in semantic_user
    assert "SUPPORTED" in semantic_system
    assert f"authoritative polarity is {expected_polarity}" in answer_system
    expected_truth = "true" if binary_verdict == "TRUE" else "false"
    assert f"proposition is {expected_truth}" in answer_system
    assert "must not restate the proposition with the opposite truth value" in answer_system
    assert "explanation body only" in answer_system
    assert query in answer_user


@pytest.mark.asyncio
async def test_binary_answer_uses_exact_retrieved_citation_quote(monkeypatch):
    evidence = (
        "Students have the option to send recommendations to Georgia Tech. "
        "This is completely optional."
    )
    response = (
        '{"answer":"recommendation letters are optional.","citations":['
        '{"url":"https://example.gatech.edu/recommendations","title":"Recommendations",'
        '"fetched_at":null,"quote":"Recommendations are optional."}],'
        '"confidence":1.0,"notes":[]}'
    )
    call = AsyncMock(side_effect=["Recommendation letters are required.", "CONTRADICTED", response])
    monkeypatch.setattr("app.rag.answerer._call_llm", call)

    answer = await generate_answer(
        "Do I have to submit recommendation letters?",
        [_chunk("recommendations", "Recommendations", evidence)],
        intent="policy",
    )

    assert answer["answer"] == "No, recommendation letters are optional."
    assert answer["citations"][0]["quote"] == evidence
    assert answer["citations"][0]["quote"] in evidence


@pytest.mark.asyncio
async def test_binary_precheck_logs_exact_proposition_and_evidence(monkeypatch):
    evidence = "The OMSCS degree requires students to complete 30 total credit hours (10 courses)."
    proposition = "Completing nine courses is enough to graduate."
    debug = Mock()
    monkeypatch.setattr("app.rag.answerer.logger.debug", debug)
    monkeypatch.setattr(
        "app.rag.answerer._call_llm",
        AsyncMock(
            side_effect=[
                proposition,
                "CONTRADICTED",
                '{"answer":"10 courses are required.","citations":[],"confidence":1.0,"notes":[]}',
            ]
        ),
    )

    await generate_answer(
        "Is completing nine OMSCS courses enough to graduate?",
        [_chunk("degree-requirements", "Degree Requirements", evidence)],
        intent="policy",
    )

    logged = debug.call_args.kwargs
    assert logged["proposition"] == proposition
    assert evidence in logged["evidence"]


@pytest.mark.asyncio
async def test_binary_citation_prefers_claim_supporting_chunk_over_exact_unrelated_quote(
    monkeypatch,
):
    unrelated = _chunk(
        "application-documents",
        "Application Documents",
        "These documents could include recommendations, required or optional portfolios.",
    )
    decisive = _chunk(
        "recommendations",
        "Recommendations",
        "Students have the option to send recommendations to Georgia Tech. "
        "This is completely optional.",
    )
    response = (
        '{"answer":"recommendations are optional.","citations":['
        f'{{"url":"{unrelated.url}","title":"Application Documents","fetched_at":null,'
        f'"quote":"{unrelated.chunk_text}"}}],"confidence":1.0,"notes":[]}}'
    )
    monkeypatch.setattr(
        "app.rag.answerer._call_llm",
        AsyncMock(side_effect=["Recommendations are required.", "CONTRADICTED", response]),
    )

    answer = await generate_answer(
        "Do I have to submit recommendations?", [unrelated, decisive], intent="policy"
    )

    assert answer["citations"][0]["url"] == decisive.url
    assert "completely optional" in answer["citations"][0]["quote"]
    assert answer["citations"][0]["quote"] in decisive.chunk_text


@pytest.mark.asyncio
@pytest.mark.parametrize("semantic_verdict", ["INSUFFICIENT", "SUPPORTED because optional", ""])
async def test_factual_yes_no_unknown_or_malformed_verdict_abstains(monkeypatch, semantic_verdict):
    call = AsyncMock(side_effect=["This policy is required.", semantic_verdict])
    monkeypatch.setattr("app.rag.answerer._call_llm", call)

    answer = await generate_answer(
        "Is this policy required?",
        [_chunk("policy", "Policy", "The available evidence does not address that policy.")],
        intent="policy",
    )

    assert call.await_count == 2
    assert answer["citations"] == []
    assert answer["confidence"] == 0.2
    assert "enough evidence" in answer["answer"].lower()


@pytest.mark.asyncio
async def test_factual_yes_no_verifier_error_abstains(monkeypatch):
    monkeypatch.setattr(
        "app.rag.answerer._call_llm",
        AsyncMock(side_effect=RuntimeError("provider unavailable")),
    )

    answer = await generate_answer(
        "Is this policy required?",
        [_chunk("policy", "Policy", "The evidence does not address that policy.")],
        intent="policy",
    )

    assert answer["citations"] == []
    assert answer["confidence"] == 0.2
