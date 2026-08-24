import pytest

from eval.quality.diagnose_policy_evidence import classify_root_cause


@pytest.mark.parametrize(
    ("facts", "expected"),
    (
        ({"gold_span": False}, ("GOLD_OR_EVAL_DEFINITION_ISSUE", "gold_definition")),
        (
            {"document_exists": False},
            ("DOCUMENT_NOT_INDEXED", "corpus_source_availability"),
        ),
        (
            {"span_in_document": False},
            ("STALE_OR_INCOMPLETE_INGESTION", "corpus_source_availability"),
        ),
        (
            {"span_in_chunk": False},
            ("CHUNKING_BOUNDARY_LOSS", "chunk_availability"),
        ),
        (
            {"source_in_route": False},
            ("SOURCE_ROUTING_LOSS", "candidate_generation"),
        ),
        (
            {"production_evidence_rank": 4},
            ("RESOLVED_SINCE_FREEZE", "resolved"),
        ),
        (
            {"parent_evidence_rank": 3},
            ("CHILD_RESELECTION_LOSS", "child_reselection"),
        ),
        (
            {"production_document_rank": 2},
            ("DOCUMENT_RETRIEVED_WRONG_CHUNK", "chunk_selection"),
        ),
        (
            {"parent_document_rank": 2},
            ("DOCUMENT_RETRIEVED_WRONG_CHUNK", "chunk_selection"),
        ),
        (
            {"pre_rerank_evidence_rank": 8},
            ("FUSION_OR_RERANK_LOSS", "fusion_rerank_top_k"),
        ),
        (
            {"candidate_generated": True},
            ("FUSION_OR_RERANK_LOSS", "fusion_rerank_top_k"),
        ),
        (
            {"deep_vector_evidence_rank": 42},
            ("CANDIDATE_GENERATION_TRUNCATION", "candidate_generation"),
        ),
        (
            {"deep_fts_or_evidence_rank": 17},
            ("CANDIDATE_GENERATION_TRUNCATION", "candidate_generation"),
        ),
        ({}, ("QUERY_TERM_MISMATCH", "candidate_generation")),
    ),
)
def test_classify_root_cause_uses_earliest_failed_stage(facts, expected):
    defaults = {
        "gold_span": True,
        "document_exists": True,
        "span_in_document": True,
        "span_in_chunk": True,
        "source_in_route": True,
        "production_evidence_rank": None,
        "production_document_rank": None,
        "parent_evidence_rank": None,
        "parent_document_rank": None,
        "candidate_generated": False,
        "pre_rerank_evidence_rank": None,
        "deep_vector_evidence_rank": None,
        "deep_fts_and_evidence_rank": None,
        "deep_fts_or_evidence_rank": None,
    }

    assert classify_root_cause(**(defaults | facts)) == expected
