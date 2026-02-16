"""Tests for citation grounding check."""

from app.rag.grounding import check_grounding, _is_grounded
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
