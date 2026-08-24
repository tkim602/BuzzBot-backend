from app.rag.retrieval import RetrievedChunk
from eval.quality.policy_oracle_retrieval import (
    architectural_decision,
    group_unresolved_categories,
    rank_document_chunks,
    summarize_evidence_ranks,
)


def _chunk(chunk_id: str, url: str, text: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        url=url,
        title="Policy",
        headings="Requirements",
        chunk_text=text,
        score=score,
        source_name="gt-policy",
    )


def test_rank_document_chunks_keeps_only_gold_document_and_top_five(monkeypatch):
    monkeypatch.setattr("eval.quality.policy_oracle_retrieval.settings.rag_enable_reranking", False)
    gold_url = "https://example.gatech.edu/policy/"
    other_url = "https://example.gatech.edu/other"
    gold = [
        _chunk(str(index), gold_url, f"requirement evidence {index}", 1.0 - index / 10)
        for index in range(7)
    ]
    unrelated = _chunk("other", other_url, "requirement evidence", 2.0)

    ranked = rank_document_chunks(
        "what is the requirement?",
        gold_url,
        [unrelated, *gold],
        [unrelated, *gold],
        top_k=5,
    )

    assert len(ranked) == 5
    assert {chunk.url for chunk in ranked} == {gold_url}


def test_summarize_evidence_ranks_reports_fixed_cutoffs_and_latency():
    summary = summarize_evidence_ranks(
        [1, 3, 5, None],
        [10.0, 20.0, 30.0, 100.0],
    )

    assert summary == {
        "cases": 4,
        "evidence_hit_at_1": 0.25,
        "evidence_hit_at_3": 0.5,
        "evidence_hit_at_5": 0.75,
        "evidence_mrr_at_5": 0.3833333333333333,
        "latency_ms": {"mean": 40.0, "p95": 100.0},
    }


def test_group_unresolved_categories_excludes_resolved_cases():
    rows = [
        {"case_id": "a", "oracle_evidence_rank": 1},
        {"case_id": "b", "oracle_evidence_rank": None},
        {"case_id": "c", "oracle_evidence_rank": 4},
    ]
    categories = {
        "a": "DOCUMENT_RETRIEVED_WRONG_CHUNK",
        "b": "DOCUMENT_RETRIEVED_WRONG_CHUNK",
        "c": "RESOLVED_SINCE_FREEZE",
    }

    assert group_unresolved_categories(rows, categories) == {
        "DOCUMENT_RETRIEVED_WRONG_CHUNK": {
            "cases": 2,
            "evidence_hit_at_1": 0.5,
            "evidence_hit_at_3": 0.5,
            "evidence_hit_at_5": 0.5,
            "evidence_mrr_at_5": 0.5,
        }
    }


def test_architectural_decision_uses_predeclared_ninety_percent_gate():
    assert architectural_decision(0.90) == "HIERARCHICAL_RETRIEVAL_SUPPORTED"
    assert architectural_decision(0.89) == "REPRESENTATION_OR_WITHIN_DOCUMENT_RANKING"
