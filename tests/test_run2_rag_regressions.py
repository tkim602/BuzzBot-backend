from unittest.mock import AsyncMock, Mock

import pytest

from app.core.config import settings
from app.rag import retrieval
from app.rag.answerer import _ground_citation_quotes, generate_answer
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


def test_recommendation_letters_query_outranks_generic_deadline_page():
    query = (
        "Do I have to submit recommendation letters when applying to Georgia Tech "
        "as a first-year student?"
    )
    recommendations = _chunk(
        "recommendations",
        "Recommendations | Undergraduate Admission",
        "Students have the option to send recommendations to Georgia Tech. "
        "This is completely optional.",
        "Recommendations",
    )
    deadlines = _chunk(
        "deadlines",
        "First-Year Application Plans and Deadlines | Undergraduate Admission",
        "Georgia Tech uses the Common Application for all first-year applicants. "
        "The application review process is the same in Early Action and Regular Decision.",
        "First-Year Admission",
    )

    assert _lexical_match_score(query, recommendations) > _lexical_match_score(query, deadlines)


def test_omscs_claim_selects_degree_requirements_not_workload_faq():
    claim = "OMSCS requires 30 credit hours for graduation, which is equivalent to 10 courses."
    degree = _chunk(
        "degree-requirements",
        "Degree Requirements | Online Master of Science in Computer Science (OMSCS)",
        "The OMSCS degree requires students to complete 30 total credit hours (10 courses).",
        "Degree Requirements",
    )
    faq = _chunk(
        "prospective-student-faqs",
        "Prospective Student FAQs | Online Master of Science in Computer Science (OMSCS)",
        "About OMSCS. When can students sign up for courses? What courses are available? "
        "How many total hours are required? How does the student workload compare to a "
        "residential degree? How many hours a week should students expect to spend on it?",
        "About OMSCS",
    )
    citations = [{"url": faq.url, "title": faq.title, "quote": faq.chunk_text, "fetched_at": None}]

    selected = _ground_citation_quotes(citations, [faq, degree], claim)

    assert selected[0]["url"] == degree.url
    assert selected[0]["quote"] == degree.chunk_text


def test_claim_span_selection_ignores_unrelated_text_from_same_source():
    claim = "Recommendations are optional."
    url = "https://example.gatech.edu/first-year/recommendations"
    unrelated = RetrievedChunk(
        chunk_id="unrelated",
        url=url,
        title="Recommendations",
        headings="Application Review",
        chunk_text=(
            "The application review process is the same in both Early Action "
            "and Regular Decision plans."
        ),
        source_name="gt-admission",
        score=0.9,
    )
    decisive = RetrievedChunk(
        chunk_id="decisive",
        url=url,
        title="Recommendations",
        headings="Recommendations",
        chunk_text=(
            "Students have the option to send recommendations to Georgia Tech. "
            "This is completely optional."
        ),
        source_name="gt-admission",
        score=0.8,
    )
    citations = [
        {
            "url": url,
            "title": "Recommendations",
            "quote": unrelated.chunk_text,
            "fetched_at": None,
        }
    ]

    selected = _ground_citation_quotes(citations, [unrelated, decisive], claim)

    assert "completely optional" in selected[0]["quote"]
    assert unrelated.chunk_text not in selected[0]["quote"]


def test_negative_claim_cites_span_with_matching_polarity():
    claim = "Registering a graduate internship does not charge tuition."
    url = "https://example.gatech.edu/register-internships"
    distractor = _chunk(
        "overview",
        "Graduate Internship Program",
        "Flexibility Without Delay. In short, registering your graduate internship at "
        "Georgia Tech protects your status and academic record.",
    )
    decisive = _chunk(
        "tuition",
        "Graduate Internship Program",
        "There is no tuition associated with participation in the graduate internship program.",
    )
    distractor.url = decisive.url = url

    selected = _ground_citation_quotes(
        [{"url": url, "quote": distractor.chunk_text}],
        [distractor, decisive],
        claim,
    )

    assert selected[0]["quote"] == decisive.chunk_text


def test_multi_claim_answer_selects_support_for_each_claim():
    ordering = _chunk(
        "ordering",
        "Ordering Transcripts",
        "Current or former Georgia Tech students may order transcripts via the web. "
        "Students may request a transcript as a downloadable PDF, mailed, or picked up.",
    )
    policies = _chunk(
        "policies",
        "Transcript Policies",
        "The transcript fee is $10.00. The institute is not responsible for delivery. "
        "Transcript requests should be made at least one week in advance. "
        "Transcripts will not be released for students who have a financial obligation.",
    )
    answer = (
        "Students may order transcripts through the web. "
        "Requests may be downloaded, mailed, or picked up. "
        "The fee is $10.00, and requests should be made at least one week in advance. "
        "Transcripts are not released for students with a financial obligation."
    )
    citations = [{"url": ordering.url, "quote": ordering.chunk_text}] * 3

    selected = _ground_citation_quotes(citations, [ordering, policies], answer)
    evidence = "\n".join(str(citation["quote"]) for citation in selected)

    assert "order transcripts via the web" in evidence
    assert "downloadable PDF, mailed, or picked up" in evidence
    assert "transcript fee is $10.00" in evidence
    assert "at least one week in advance" in evidence
    assert "financial obligation" in evidence


def test_citation_span_selection_prefers_exact_numeric_evidence_across_chunks():
    distractor = _chunk(
        "immunization-form",
        "Fall 2026 Immunization Deadlines",
        "All students must submit an immunization form before registration.",
        "Fall 2026 Deadlines",
    )
    decisive = _chunk(
        "immunization-requirements",
        "Immunization Requirements",
        "Fall 2026 Deadlines: Last names beginning with A – H: June 8, 2026. "
        "Last names beginning with I – P: June 22, 2026. "
        "Last names beginning with Q – Z: July 6, 2026.",
    )
    answer = (
        "For Fall 2026, A-H is due June 8, 2026; I-P is due June 22, 2026; Q-Z is due July 6, 2026."
    )

    selected = _ground_citation_quotes(
        [{"url": distractor.url, "quote": distractor.chunk_text}],
        [distractor, decisive],
        answer,
    )
    evidence = "\n".join(str(citation["quote"]) for citation in selected)

    assert "A – H: June 8, 2026" in evidence
    assert "I – P: June 22, 2026" in evidence
    assert "Q – Z: July 6, 2026" in evidence


@pytest.mark.asyncio
async def test_claim_validation_receives_decisive_recommendation_evidence(monkeypatch):
    claim = "Recommendations are optional."
    decisive = _chunk(
        "recommendations",
        "Recommendations",
        "Students have the option to send recommendations to Georgia Tech. "
        "This is completely optional.",
        "Recommendations",
    )
    unrelated = _chunk(
        "deadlines",
        "First-Year Application Plans and Deadlines",
        "The application review process is the same in Early Action and Regular Decision.",
    )
    selected = _ground_citation_quotes(
        [{"url": unrelated.url, "title": unrelated.title, "quote": unrelated.chunk_text}],
        [unrelated, decisive],
        claim,
    )
    verifier = AsyncMock(return_value="SUPPORTED")
    monkeypatch.setattr("app.rag.grounding._call_llm", verifier)

    supported, notes = await check_claim_support(
        claim,
        [unrelated, decisive],
        min_overlap_ratio=1.1,
        citations=selected,
    )

    semantic_input = verifier.await_args.args[1]
    assert supported
    assert not notes
    assert "completely optional" in semantic_input
    assert "same in Early Action and Regular Decision" not in semantic_input


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
async def test_indirect_whether_question_uses_binary_verdict(monkeypatch):
    evidence = (
        "Electronic Check/WebCheck (FREE). Georgia Tech offers the ability to pay "
        "by electronic check from a U.S. bank account."
    )
    call = AsyncMock(
        side_effect=[
            "WebCheck carries a payment fee.",
            "CONTRADICTED",
            '{"answer":"WebCheck is free.","citations":[{"url":"x","quote":"x"}],'
            '"confidence":1.0,"notes":[]}',
        ]
    )
    monkeypatch.setattr("app.rag.answerer._call_llm", call)

    answer = await generate_answer(
        "I'm dealing with whether WebCheck carries a payment fee right now.",
        [_chunk("payment-options", "Payment Options", evidence)],
        intent="policy",
    )

    assert call.await_count == 3
    assert answer["_binary_verdict"] == "FALSE"
    assert answer["answer"].startswith("No,")


@pytest.mark.asyncio
async def test_factual_prompt_requires_every_requested_field_without_adjacent_rules(monkeypatch):
    call = AsyncMock(
        return_value='{"answer":"30 credits and 10 courses.","citations":[],'
        '"confidence":1.0,"notes":[]}'
    )
    monkeypatch.setattr("app.rag.answerer._call_llm", call)

    await generate_answer(
        "What are the total degree hours and courses?",
        [_chunk("degree", "Degree Requirements", "30 total credit hours (10 courses).")],
        intent="policy",
    )

    system_prompt = call.await_args.args[0]
    assert "every requested field" in system_prompt
    assert "unrequested adjacent rules" in system_prompt


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
