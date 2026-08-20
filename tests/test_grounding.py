"""Tests for citation grounding check."""

from unittest.mock import AsyncMock

import pytest

from app.rag import grounding
from app.rag.grounding import _is_grounded, check_claim_support, check_grounding
from app.rag.retrieval import RetrievedChunk


def _make_chunk(text: str, url: str = "https://example.com") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="test-1",
        url=url,
        title="Test",
        chunk_text=text,
        score=0.9,
    )


def test_exact_substring_grounded():
    assert _is_grounded("registration deadline", "The registration deadline is January 5.", 0.5)


def test_no_overlap_not_grounded():
    assert not _is_grounded("quantum physics theory", "The cat sat on the mat.", 0.5)


def test_check_grounding_keeps_valid():
    chunks = [_make_chunk("The fall semester registration deadline is August 15, 2025.")]
    citations = [
        {"url": "https://example.com", "quote": "registration deadline is August 15"},
    ]
    valid, notes = check_grounding(citations, chunks)
    assert len(valid) == 1
    assert len(notes) == 0


def test_check_grounding_removes_invalid():
    chunks = [_make_chunk("Course CS 1332 covers data structures and algorithms.")]
    citations = [
        {"url": "https://example.com", "quote": "quantum computing is available next semester"},
    ]
    valid, notes = check_grounding(citations, chunks)
    assert len(valid) == 0
    assert len(notes) == 1


def test_empty_citations():
    valid, notes = check_grounding([], [])
    assert valid == []
    assert notes == []


def test_empty_quote_removed():
    chunks = [_make_chunk("Some text.")]
    citations = [{"url": "https://example.com", "quote": ""}]
    valid, notes = check_grounding(citations, chunks)
    assert len(valid) == 0
    assert len(notes) == 1


def test_url_not_in_retrieved_chunks_removed():
    chunks = [_make_chunk("Registration opens on January 8.", url="https://a.example")]
    citations = [{"url": "https://b.example", "quote": "Registration opens on January 8"}]
    valid, notes = check_grounding(citations, chunks)
    assert len(valid) == 0
    assert len(notes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evidence", "claim", "verdict", "expected", "verifier_calls"),
    [
        (
            "This is completely optional.",
            "Recommendation letters are not required.",
            "SUPPORTED",
            True,
            1,
        ),
        (
            "This is completely optional.",
            "Recommendation letters are required.",
            "CONTRADICTED",
            False,
            1,
        ),
        (
            "We can only accept up to three recommendations per applicant.",
            "Applicants may submit up to three recommendations.",
            "SUPPORTED",
            True,
            0,
        ),
        (
            "We can only accept up to three recommendations per applicant.",
            "Applicants may submit four recommendations.",
            "CONTRADICTED",
            False,
            1,
        ),
        (
            "You do not apply to a specific major or college.",
            "You apply directly to a specific major.",
            "CONTRADICTED",
            False,
            1,
        ),
        (
            "A complete application includes the Common Application, transcript, and test scores.",
            "Recommendation letters are required for a complete application.",
            "INSUFFICIENT",
            False,
            1,
        ),
        (
            "The OMSCS degree requires 30 total credit hours (10 courses).",
            "Nine courses are enough to graduate.",
            "CONTRADICTED",
            False,
            1,
        ),
        (
            "A cumulative GPA of 3.0 is required to graduate.",
            "A student can graduate with a GPA below 3.0, even if you complete all 10 courses.",
            "CONTRADICTED",
            False,
            1,
        ),
        (
            "Early Action 1 — Application Deadline: October 15\n"
            "Early Action 2 — Application Deadline: November 2\n"
            "Regular Decision — Application Deadline: January 6",
            "November 2 is the Early Action 1 deadline.",
            "CONTRADICTED",
            False,
            1,
        ),
    ],
)
async def test_claim_support_uses_strict_semantic_fallback(
    monkeypatch, evidence, claim, verdict, expected, verifier_calls
):
    call = AsyncMock(return_value=verdict)
    monkeypatch.setattr("app.rag.grounding._call_llm", call)

    supported, notes = await check_claim_support(claim, [_make_chunk(evidence)])

    assert supported is expected
    assert (not notes) if expected else bool(notes)
    assert call.await_count == verifier_calls
    if verifier_calls:
        system, user = call.await_args.args
        assert claim.rstrip(".") in user
        assert evidence in user
        assert call.await_args.kwargs == {"temperature": 0.0, "max_tokens": 8}
        assert "outside knowledge" in system
        assert "absence" in system


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["SUPPORTED because optional", "", "UNKNOWN"])
async def test_claim_support_fails_closed_on_malformed_verifier_output(monkeypatch, verdict):
    monkeypatch.setattr("app.rag.grounding._call_llm", AsyncMock(return_value=verdict))

    supported, notes = await check_claim_support(
        "Recommendation letters are not required.",
        [_make_chunk("This is completely optional.")],
    )

    assert not supported
    assert notes


@pytest.mark.asyncio
async def test_claim_support_fails_closed_on_verifier_error(monkeypatch):
    monkeypatch.setattr(
        "app.rag.grounding._call_llm",
        AsyncMock(side_effect=RuntimeError("provider unavailable")),
    )

    supported, notes = await check_claim_support(
        "Recommendation letters are not required.",
        [_make_chunk("This is completely optional.")],
    )

    assert not supported
    assert notes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "evidence", "answer", "verdict", "expected"),
    [
        (
            "Do I have to submit recommendation letters?",
            "Recommendations are completely optional.",
            "No, recommendation letters are optional.",
            "CONSISTENT",
            True,
        ),
        (
            "Are nine courses enough to graduate?",
            "The degree requires 10 courses.",
            "No, 10 courses are required.",
            "CONSISTENT",
            True,
        ),
        (
            "Is November 2 the Early Action 1 deadline?",
            "Early Action 1 — Application Deadline: October 15\n"
            "Early Action 2 — Application Deadline: November 2",
            "No. Early Action 1 is October 15; November 2 is Early Action 2.",
            "CONSISTENT",
            True,
        ),
        (
            "Is November 2 the Early Action 1 deadline?",
            "Early Action 1 — Application Deadline: October 15\n"
            "Early Action 2 — Application Deadline: November 2",
            "Yes, November 2 is the Early Action 2 deadline.",
            "INCONSISTENT",
            False,
        ),
    ],
)
async def test_yes_no_consistency_uses_question_answer_and_evidence(
    monkeypatch, question, evidence, answer, verdict, expected
):
    call = AsyncMock(return_value=verdict)
    monkeypatch.setattr("app.rag.grounding._call_llm", call)

    consistent, notes = await grounding.check_yes_no_consistency(
        question, answer, [_make_chunk(evidence)]
    )

    assert consistent is expected
    assert (not notes) if expected else bool(notes)
    system, user = call.await_args.args
    assert question in user
    assert answer in user
    assert evidence in user
    assert "exact proposition" in system
    assert call.await_args.kwargs == {"temperature": 0.0, "max_tokens": 8}


@pytest.mark.asyncio
async def test_yes_no_consistency_fails_closed_on_malformed_verdict(monkeypatch):
    monkeypatch.setattr(
        "app.rag.grounding._call_llm", AsyncMock(return_value="CONSISTENT because correct")
    )

    consistent, notes = await grounding.check_yes_no_consistency(
        "Is this required?", "Yes, it is required.", [_make_chunk("It is required.")]
    )

    assert not consistent
    assert notes


@pytest.mark.asyncio
async def test_yes_no_consistency_fails_closed_on_verifier_error(monkeypatch):
    monkeypatch.setattr(
        "app.rag.grounding._call_llm",
        AsyncMock(side_effect=RuntimeError("provider unavailable")),
    )

    consistent, notes = await grounding.check_yes_no_consistency(
        "Is this required?", "Yes, it is required.", [_make_chunk("It is required.")]
    )

    assert not consistent
    assert notes
