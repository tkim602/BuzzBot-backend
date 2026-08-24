import pytest

from app.rag.retrieval import RetrievedChunk
from eval.quality.policy_hierarchical_retrieval import (
    DOCUMENT_COUNTS,
    merge_document_candidates,
    select_candidate,
    select_document_urls,
)
from eval.quality.policy_oracle_retrieval import rank_document_chunks


def _chunk(chunk_id: str, url: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        url=url,
        title="Policy",
        headings="Requirements",
        chunk_text=f"evidence {chunk_id}",
        score=score,
        source_name="gt-policy",
    )


def test_document_counts_are_the_only_bounded_comparison_axis():
    assert DOCUMENT_COUNTS == (1, 2, 3, 5)


def test_select_document_urls_uses_ranked_prefix_without_gold_input():
    ranked = [
        _chunk("a1", "https://example.gatech.edu/a/", 1.0),
        _chunk("a2", "https://example.gatech.edu/a", 0.9),
        _chunk("b", "https://example.gatech.edu/b", 0.8),
        _chunk("c", "https://example.gatech.edu/c", 0.7),
    ]

    assert select_document_urls(ranked, 2) == (
        "https://example.gatech.edu/a/",
        "https://example.gatech.edu/b",
    )

    with pytest.raises(ValueError, match="document_count"):
        select_document_urls(ranked, 4)


def test_within_document_ranking_can_defer_cross_document_rerank(monkeypatch):
    monkeypatch.setattr(
        "eval.quality.policy_oracle_retrieval.rerank_with_cross_encoder",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected rerank")),
    )
    url = "https://example.gatech.edu/a"

    ranked = rank_document_chunks(
        "evidence",
        url,
        [_chunk("a", url, 0.9)],
        [_chunk("a", url, 0.9)],
        top_k=15,
        rerank=False,
    )

    assert [chunk.chunk_id for chunk in ranked] == ["a"]


def test_cross_document_merge_filters_selected_urls_and_returns_five(monkeypatch):
    monkeypatch.setattr(
        "eval.quality.policy_hierarchical_retrieval.rerank_with_cross_encoder",
        lambda _query, chunks, top_k: sorted(chunks, key=lambda chunk: chunk.score, reverse=True)[
            :top_k
        ],
    )
    selected = ("https://example.gatech.edu/a", "https://example.gatech.edu/b")
    candidates = [
        *[_chunk(f"a-{index}", selected[0], float(index)) for index in range(4)],
        *[_chunk(f"b-{index}", selected[1], float(index)) for index in range(4)],
        _chunk("other", "https://example.gatech.edu/other", 100.0),
    ]

    merged = merge_document_candidates("question", selected, candidates, top_k=5)

    assert len(merged) == 5
    assert {chunk.url for chunk in merged} <= set(selected)
    assert "other" not in {chunk.chunk_id for chunk in merged}


def test_select_candidate_uses_best_passing_hit_rate_then_smallest_n():
    summaries = {
        1: {"evidence_hit_at_5": 0.84},
        2: {"evidence_hit_at_5": 0.88},
        3: {"evidence_hit_at_5": 0.90},
        5: {"evidence_hit_at_5": 0.90},
    }

    assert select_candidate(summaries) == {
        "document_count": 3,
        "evidence_hit_at_5": 0.90,
        "minimum_gate_passed": True,
        "stretch_gate_passed": True,
    }
    assert select_candidate({1: {"evidence_hit_at_5": 0.80}}) is None
