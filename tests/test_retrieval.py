"""Tests for retrieval query hints and fusion logic."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.rag.retrieval import (
    FTS_DOCUMENT_EXPRESSION,
    RetrievedChunk,
    _cap_chunks_per_url,
    _compact_query_for_fts,
    _extract_query_hints,
    _rrf_fuse_results,
    _signal_match_count,
    get_text_embeddings,
    hybrid_retrieve,
)


def test_fts_query_expression_matches_migration_index():
    migration = Path("db/migrations/versions/005_document_fts_metadata.py").read_text()

    assert FTS_DOCUMENT_EXPRESSION == (
        "coalesce(title, '') || ' ' || coalesce(headings, '') || ' ' || chunk_text"
    )
    assert FTS_DOCUMENT_EXPRESSION in migration


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


def test_candidate_cap_preserves_order_and_treats_missing_urls_independently():
    chunks = [
        RetrievedChunk("a-1", "https://example.com/a", None, "a1", 1.0),
        RetrievedChunk("a-2", "https://example.com/a/", None, "a2", 0.9),
        RetrievedChunk("b-1", "https://example.com/b", None, "b1", 0.8),
        RetrievedChunk("none-1", None, None, "n1", 0.7),
        RetrievedChunk("none-2", None, None, "n2", 0.6),
    ]

    capped = _cap_chunks_per_url(chunks, max_chunks_per_url=1, top_k=10)

    assert [chunk.chunk_id for chunk in capped] == ["a-1", "b-1", "none-1", "none-2"]


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
async def test_reranker_receives_candidates_beyond_final_top_k(monkeypatch):
    vector_results = [_chunk(f"generic-{index}", 1.0 - index / 10, "vector") for index in range(5)]
    recommendation = RetrievedChunk(
        chunk_id="recommendations",
        url="https://example.com/first-year/recommendations",
        title="Recommendations",
        headings="Recommendations",
        chunk_text="Recommendations are completely optional.",
        score=0.1,
        method="fts",
    )
    fts_results = [
        *[_chunk(f"fts-generic-{index}", 1.0 - index / 10, "fts") for index in range(6)],
        recommendation,
    ]

    async def search_fts(*args, top_k, **kwargs):
        return fts_results[:top_k]

    monkeypatch.setattr("app.rag.retrieval.vector_search", AsyncMock(return_value=vector_results))
    monkeypatch.setattr("app.rag.retrieval.fts_search", search_fts)
    monkeypatch.setattr(settings, "rag_enable_reranking", True)

    def rerank(query, chunks, top_k):
        assert recommendation in chunks
        return [recommendation, *[chunk for chunk in chunks if chunk is not recommendation]][:top_k]

    monkeypatch.setattr("app.rag.retrieval.rerank_with_cross_encoder", rerank)

    results = await hybrid_retrieve(
        object(),
        "Do I have to submit recommendation letters?",
        [0.1],
        top_k=5,
        force_fts=True,
    )

    assert results[0] is recommendation


@pytest.mark.asyncio
async def test_document_cap_backfills_fts_candidates_before_reranking(monkeypatch):
    duplicate_chunks = [
        RetrievedChunk(
            chunk_id=f"duplicate-{index}",
            url="https://example.com/duplicate",
            title="Duplicate",
            chunk_text=f"Duplicate chunk {index}",
            score=1.0 - index / 100,
            method="fts",
        )
        for index in range(16)
    ]
    relevant = RetrievedChunk(
        chunk_id="relevant",
        url="https://example.com/relevant",
        title="Relevant",
        chunk_text="The decisive answer.",
        score=0.1,
        method="fts",
    )
    requested: dict[str, int] = {}

    async def search_fts(*args, top_k, **kwargs):
        requested["top_k"] = top_k
        return [*duplicate_chunks, relevant][:top_k]

    monkeypatch.setattr("app.rag.retrieval.vector_search", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.rag.retrieval.fts_search", search_fts)
    monkeypatch.setattr(settings, "rag_enable_reranking", True)

    def rerank(query, chunks, top_k):
        assert relevant in chunks
        return [relevant, *[chunk for chunk in chunks if chunk is not relevant]][:top_k]

    monkeypatch.setattr("app.rag.retrieval.rerank_with_cross_encoder", rerank)

    results = await hybrid_retrieve(
        object(),
        "decisive answer",
        [0.1],
        top_k=5,
        force_fts=True,
        max_chunks_per_url=1,
    )

    assert requested["top_k"] == 45
    assert results[0] is relevant


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
