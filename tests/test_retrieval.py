"""Tests for retrieval query hints and fusion logic."""

from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.rag.retrieval import (
    RetrievedChunk,
    _compact_query_for_fts,
    _extract_query_hints,
    _rrf_fuse_results,
    _signal_match_count,
    get_text_embeddings,
)


def _chunk(chunk_id: str, score: float, method: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        url="https://example.com",
        title="Example",
        chunk_text=f"Chunk {chunk_id}",
        score=score,
        method=method,
    )


def test_extract_query_hints_for_course_and_term():
    hints = _extract_query_hints("Will CS4400 be offered in 2025 Spring?")
    assert hints.course_code == "CS 4400"
    assert hints.term_name == "Spring 2025"
    assert "CS4400" in hints.expanded_query
    assert "CS-4400" in hints.expanded_query


def test_extract_query_hints_without_matches():
    hints = _extract_query_hints("Tell me about Georgia Tech dining options.")
    assert hints.course_code is None
    assert hints.term_name is None


def test_extract_query_hints_ignores_fall_year_as_course_code():
    hints = _extract_query_hints("When is the registration deadline for Fall 2026?")
    assert hints.course_code is None
    assert hints.term_name == "Fall 2026"


def test_extract_query_hints_term_with_korean_suffix():
    hints = _extract_query_hints("CS4400 수업이 2025 Spring에 offered 되나요?")
    assert hints.course_code == "CS 4400"
    assert hints.term_name == "Spring 2025"


def test_rrf_fusion_promotes_consensus_results():
    vector_results = [
        _chunk("a", 0.8, "vector"),
        _chunk("b", 0.7, "vector"),
    ]
    fts_results = [
        _chunk("b", 0.9, "fts"),
        _chunk("c", 0.6, "fts"),
    ]

    merged = _rrf_fuse_results(vector_results, fts_results, top_k=3, k=10)
    assert merged[0].chunk_id == "b"
    assert merged[0].method == "hybrid_rrf"
    assert {m.chunk_id for m in merged} == {"a", "b", "c"}


def test_compact_query_for_fts_limits_tokens():
    q = "CS 4400 offered in Spring 2025 with instructor info and section schedule and waitlist"
    compact = _compact_query_for_fts(q, max_tokens=6)
    assert len(compact.split()) <= 6


def test_signal_match_count_detects_course_and_term():
    hints = _extract_query_hints("CS 4400 Spring 2025 offered?")
    chunk = RetrievedChunk(
        chunk_id="x",
        url="https://example.com",
        title="CS 4400 - Spring 2025",
        chunk_text="Course: CS 4400 Term: Spring 2025",
        score=0.1,
    )
    assert _signal_match_count(chunk, hints) == 2


def test_signal_match_count_boosts_course_summary_for_availability():
    hints = _extract_query_hints("Is CS 4400 offered in Spring 2025?")
    chunk = RetrievedChunk(
        chunk_id="summary",
        url="https://example.com",
        title="CS 4400 summary",
        chunk_text="CS 4400 is offered in Spring 2025.",
        score=0.1,
        metadata_json={"type": "course_summary"},
    )
    assert _signal_match_count(chunk, hints) == 3


@pytest.mark.asyncio
async def test_async_embedding_client_receives_key_loaded_by_settings(monkeypatch):
    captured: dict[str, str] = {}

    class Embeddings:
        async def create(self, **kwargs):
            return SimpleNamespace(
                usage=SimpleNamespace(total_tokens=1),
                data=[SimpleNamespace(embedding=[0.1])],
            )

    class Client:
        def __init__(self, *, api_key: str):
            captured["api_key"] = api_key
            self.embeddings = Embeddings()

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "rag_enable_embedding_cache", False)
    monkeypatch.setattr("openai.AsyncOpenAI", Client)

    assert await get_text_embeddings(["test query"]) == [[0.1]]
    assert captured == {"api_key": "test-key"}
