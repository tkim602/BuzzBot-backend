"""Tests for citation grounding check."""

from unittest.mock import AsyncMock, Mock

import pytest

from app.rag.grounding import (
    _is_grounded,
    check_claim_support,
    check_grounding,
    semantic_claim_verdict,
    split_factual_claims,
)
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


def test_claim_splitter_preserves_conditions_joined_by_conjunctions():
    claim = (
        "For transfer pathway students, 30 hours must be completed after high school, "
        "and prior dual-enrollment credit does not count."
    )

    assert split_factual_claims(claim) == [claim.rstrip(".")]


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
    ("evidence", "claim", "verdict"),
    [
        (
            "10 courses are required.",
            "Nine courses are not enough.",
            "SUPPORTED",
        ),
        (
            "10 courses are required.",
            "Nine courses are enough.",
            "CONTRADICTED",
        ),
        (
            "Recommendations are completely optional.",
            "Recommendations are not required.",
            "SUPPORTED",
        ),
        (
            "Recommendations are completely optional.",
            "Recommendations are required.",
            "CONTRADICTED",
        ),
        (
            "Students have the option to send recommendations. This is completely optional.",
            "Recommendation letters are required.",
            "CONTRADICTED",
        ),
        (
            "The OMSCS degree requires 30 total credit hours (10 courses).",
            "Nine courses are not enough to graduate.",
            "SUPPORTED",
        ),
        (
            "The OMSCS degree requires 30 total credit hours (10 courses).",
            "Nine courses are enough to graduate.",
            "CONTRADICTED",
        ),
        (
            "Early Action 2 — Application Deadline: November 2.",
            "November 2 is the Early Action 2 deadline.",
            "SUPPORTED",
        ),
        (
            "Early Action 1 — October 15. Early Action 2 — November 2.",
            "November 2 is not the Early Action 1 deadline.",
            "SUPPORTED",
        ),
    ],
)
async def test_semantic_claim_verdict_is_reusable(monkeypatch, evidence, claim, verdict):
    call = AsyncMock(return_value=verdict)
    monkeypatch.setattr("app.rag.grounding._call_llm", call)

    assert await semantic_claim_verdict(claim, evidence) == verdict
    assert claim in call.await_args.args[1]
    assert evidence in call.await_args.args[1]
    assert "logical negation of the supplied claim" in call.await_args.args[0]
    assert "minimum or maximum" in call.await_args.args[0]


@pytest.mark.asyncio
async def test_claim_support_strips_answer_polarity_without_inverting_factual_negation(monkeypatch):
    call = AsyncMock(return_value="SUPPORTED")
    monkeypatch.setattr("app.rag.grounding._call_llm", call)

    supported, notes = await check_claim_support(
        "No, completing nine OMSCS courses is not enough to graduate, as a minimum of 30 credit hours is required.",
        [_make_chunk("The OMSCS degree requires 30 total credit hours (10 courses).")],
    )

    semantic_input = call.await_args.args[1]
    assert supported
    assert not notes
    assert "CLAIM:\ncompleting nine OMSCS courses is not enough" in semantic_input
    assert "CLAIM:\nNo," not in semantic_input


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
async def test_exact_but_unrelated_citation_does_not_support_claim(monkeypatch):
    citation = {
        "url": "https://example.com/documents",
        "quote": "These documents could include recommendations, required or optional portfolios.",
    }
    call = AsyncMock(return_value="INSUFFICIENT")
    monkeypatch.setattr("app.rag.grounding._call_llm", call)

    supported, notes = await check_claim_support(
        "Recommendations are optional.",
        [_make_chunk("Recommendations are completely optional.")],
        citations=[citation],
    )

    assert not supported
    assert notes
    assert citation["quote"] in call.await_args.args[1]
    assert "Recommendations are completely optional." not in call.await_args.args[1]


@pytest.mark.asyncio
async def test_rejected_omscs_claim_logs_exact_semantic_evidence_and_source(monkeypatch):
    evidence = "The OMSCS degree requires students to complete 30 total credit hours (10 courses)."
    claim = "OMSCS requires 30 credit hours for graduation, which is equivalent to 10 courses."
    source_url = "https://omscs.gatech.edu/degree-requirements"
    debug = Mock()
    monkeypatch.setattr("app.rag.grounding.logger.debug", debug)
    monkeypatch.setattr("app.rag.grounding._call_llm", AsyncMock(return_value="INSUFFICIENT"))

    supported, notes = await check_claim_support(
        claim,
        [_make_chunk(evidence, url=source_url)],
        min_overlap_ratio=1.1,
        citations=[{"url": source_url, "quote": evidence}],
    )

    assert not supported
    assert notes
    debug.assert_called_once_with(
        "factual claim rejected",
        claim=claim.rstrip("."),
        evidence=evidence,
        source_urls=[source_url],
        semantic_verdict="INSUFFICIENT",
    )
