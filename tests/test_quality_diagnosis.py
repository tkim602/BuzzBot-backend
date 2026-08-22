from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from eval.quality.diagnose_failures import (
    CorpusPresence,
    classify_retrieval_miss,
    diagnose_case,
    indexed_gold_presence,
    parse_args,
    summarize_diagnoses,
    validate_report_alignment,
    write_reports,
)
from eval.quality.schema import GoldCase


def _case(case_id: str = "gold-001-v7") -> GoldCase:
    return GoldCase(
        id=case_id,
        variant_group=case_id.rsplit("-", 1)[0],
        question="What is the rule?",
        gold_answer="The official answer.",
        gold_urls=("https://example.gatech.edu/rule",),
        gold_sources=("gt-example",),
        gold_vertical="academics",
        gold_locator="official rule",
        question_type="policy",
        time_sensitive=False,
        difficulty="realistic",
        style="direct",
    )


def _reports(cases: list[GoldCase]):
    retrieval = [
        {"case_id": case.id, "mode": mode, "rank": 1}
        for case in cases
        for mode in ("production", "raw", "vector", "fts")
    ]
    chat = [{"case_id": case.id, "status": "COMPLETED"} for case in cases]
    return retrieval, chat


def test_validate_report_alignment_accepts_one_row_per_case_and_mode():
    cases = [_case(f"gold-{index:03d}-v7") for index in range(1, 101)]
    retrieval, chat = _reports(cases)

    aligned_retrieval, aligned_chat = validate_report_alignment(cases, retrieval, chat)

    assert len(aligned_retrieval) == 100
    assert set(aligned_retrieval[cases[0].id]) == {"production", "raw", "vector", "fts"}
    assert len(aligned_chat) == 100


@pytest.mark.parametrize("missing", ["retrieval", "chat"])
def test_validate_report_alignment_rejects_missing_rows(missing):
    cases = [_case(f"gold-{index:03d}-v7") for index in range(1, 101)]
    retrieval, chat = _reports(cases)
    if missing == "retrieval":
        retrieval = retrieval[:-4]
    else:
        chat = chat[:-1]

    with pytest.raises(ValueError, match="case-ID set mismatch"):
        validate_report_alignment(cases, retrieval, chat)


def test_validate_report_alignment_rejects_duplicate_case_mode():
    cases = [_case(f"gold-{index:03d}-v7") for index in range(1, 101)]
    retrieval, chat = _reports(cases)
    retrieval.append(dict(retrieval[0]))

    with pytest.raises(ValueError, match="duplicate retrieval row"):
        validate_report_alignment(cases, retrieval, chat)


def test_validate_report_alignment_rejects_mismatched_case_sets():
    cases = [_case(f"gold-{index:03d}-v7") for index in range(1, 101)]
    retrieval, chat = _reports(cases)
    chat[-1] = {**chat[-1], "case_id": "gold-999-v7"}

    with pytest.raises(ValueError, match="case-ID set mismatch"):
        validate_report_alignment(cases, retrieval, chat)


@pytest.mark.asyncio
async def test_indexed_gold_presence_matches_only_current_documents_with_chunks():
    exact = _case("gold-001-v7")
    slash = replace(
        _case("gold-002-v7"),
        gold_urls=("https://example.gatech.edu/slash/",),
    )
    fragment = replace(
        _case("gold-003-v7"),
        gold_urls=("https://example.gatech.edu/fragment#section",),
    )
    one_of_many = replace(
        _case("gold-004-v7"),
        gold_urls=(
            "https://example.gatech.edu/missing",
            "https://example.gatech.edu/present",
        ),
    )
    no_chunks = replace(
        _case("gold-005-v7"),
        gold_urls=("https://example.gatech.edu/empty",),
    )
    absent = replace(
        _case("gold-006-v7"),
        gold_urls=("https://example.gatech.edu/absent",),
    )
    rows = [
        SimpleNamespace(doc_id="doc-1", canonical_url=exact.gold_urls[0], chunk_count=3),
        SimpleNamespace(
            doc_id="doc-2", canonical_url="https://example.gatech.edu/slash", chunk_count=2
        ),
        SimpleNamespace(
            doc_id="doc-3", canonical_url="https://example.gatech.edu/fragment", chunk_count=4
        ),
        SimpleNamespace(
            doc_id="doc-4", canonical_url="https://example.gatech.edu/present", chunk_count=5
        ),
        SimpleNamespace(
            doc_id="doc-5", canonical_url="https://example.gatech.edu/empty", chunk_count=0
        ),
    ]
    result = MagicMock()
    result.all.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    presence = await indexed_gold_presence(
        session,
        [exact, slash, fragment, one_of_many, no_chunks, absent],
    )

    assert presence[exact.id].indexed is True
    assert presence[exact.id].matching_chunk_count == 3
    assert presence[slash.id].matched_gold_urls == (slash.gold_urls[0],)
    assert presence[fragment.id].indexed is True
    assert presence[one_of_many.id].matched_gold_urls == (one_of_many.gold_urls[1],)
    assert presence[one_of_many.id].matching_document_ids == ("doc-4",)
    assert presence[no_chunks.id].indexed is False
    assert presence[absent.id].indexed is False


def _presence(case: GoldCase, indexed: bool = True) -> CorpusPresence:
    return CorpusPresence(
        case.id,
        case.gold_urls,
        case.gold_urls if indexed else (),
        indexed,
        ("doc-1",) if indexed else (),
        2 if indexed else 0,
    )


def _retrieval(production_rank=1, raw_rank=1, vector_rank=1, fts_rank=None):
    return {
        "production": {"rank": production_rank},
        "raw": {"rank": raw_rank},
        "vector": {"rank": vector_rank},
        "fts": {"rank": fts_rank},
    }


def _chat(**changes):
    return {
        "status": "COMPLETED",
        "correct": True,
        "supported": True,
        "abstained": False,
        "confidence": 0.9,
        "citation_gold_hit": True,
    } | changes


@pytest.mark.parametrize(
    ("indexed", "rank", "chat", "primary", "secondary"),
    [
        (False, 1, _chat(), "A", set()),
        (True, None, _chat(), "B", set()),
        (True, 6, _chat(), "B", set()),
        (True, 1, _chat(), "PASS", set()),
        (
            True,
            1,
            _chat(abstained=True, correct=False, supported=False, citation_gold_hit=False),
            "C",
            {
                "C_ABSTAIN_WITH_GOLD_RETRIEVED",
                "C_GOLD_RETRIEVED_NOT_CITED",
                "C_ANSWER_INCORRECT",
                "C_ANSWER_UNSUPPORTED",
            },
        ),
    ],
)
def test_diagnose_case_assigns_mutually_exclusive_primary_stage(
    indexed, rank, chat, primary, secondary
):
    case = _case()

    row = diagnose_case(case, _presence(case, indexed), _retrieval(rank), chat)

    assert row["primary_class"] == primary
    assert set(row["secondary_reasons"]) == secondary


def test_diagnose_case_records_individual_post_retrieval_reasons():
    case = _case()
    row = diagnose_case(
        case,
        _presence(case),
        _retrieval(),
        _chat(correct=False, supported=False, citation_gold_hit=False),
    )

    assert row["secondary_reasons"] == [
        "C_GOLD_RETRIEVED_NOT_CITED",
        "C_ANSWER_INCORRECT",
        "C_ANSWER_UNSUPPORTED",
    ]


def test_summarize_diagnoses_reports_conditional_rates_and_breakdowns():
    hit = diagnose_case(_case(), _presence(_case()), _retrieval(), _chat())
    missed_case = replace(_case(), id="gold-002-v7", variant_group="gold-002")
    miss = diagnose_case(
        missed_case,
        _presence(missed_case),
        _retrieval(None, None, None, None),
        _chat(correct=False, supported=False, abstained=True, citation_gold_hit=False),
    )

    summary = summarize_diagnoses([hit, miss])

    assert summary["primary_counts"] == {"A": 0, "B": 1, "C": 0, "PASS": 1, "DATA_ERROR": 0}
    assert summary["conditional"]["hit_at_5"]["correct"] == 1.0
    assert summary["conditional"]["miss_at_5"]["correct"] == 0.0
    assert summary["conditional"]["hit_at_5"]["abstained"] == 0.0
    assert summary["conditional"]["miss_at_5"]["abstained"] == 1.0
    assert summary["rank_buckets"]["rank_1"]["cases"] == 1
    assert summary["rank_buckets"]["rank_gt_10_or_missing"]["cases"] == 1
    assert summary["breakdowns"]["vertical"]["academics"]["cases"] == 2
    assert summary["breakdowns"]["vertical"]["academics"]["corpus_coverage"] == 1.0


@pytest.mark.parametrize(
    ("ranks", "expected"),
    [
        ((None, None, None), "B_NONE"),
        ((None, 2, None), "B_VECTOR_ONLY"),
        ((None, None, 2), "B_FTS_ONLY"),
        ((7, None, None), "B_RAW_BELOW_5"),
        ((2, 1, None), "B_FUSION_OR_RERANK_LOSS"),
        ((11, None, None), "B_OTHER"),
    ],
)
def test_classify_retrieval_miss_locates_channel_loss(ranks, expected):
    raw, vector, fts = ranks
    row = {
        "primary_class": "B",
        "raw_rank": raw,
        "vector_rank": vector,
        "fts_rank": fts,
    }

    assert classify_retrieval_miss(row) == expected


def test_summary_recommends_largest_failed_stage_and_counts_sources():
    case = _case()
    rows = [
        diagnose_case(case, _presence(case), _retrieval(None, None, None, None), _chat()),
        diagnose_case(
            replace(case, id="gold-002-v7", variant_group="gold-002"),
            _presence(replace(case, id="gold-002-v7", variant_group="gold-002")),
            _retrieval(None, None, 2, None),
            _chat(),
        ),
        diagnose_case(
            replace(case, id="gold-003-v7", variant_group="gold-003"),
            _presence(replace(case, id="gold-003-v7", variant_group="gold-003")),
            _retrieval(),
            _chat(correct=False),
        ),
    ]

    summary = summarize_diagnoses(rows)

    assert summary["retrieval_miss_subtypes"] == {"B_NONE": 1, "B_VECTOR_ONLY": 1}
    assert summary["retrieval_miss_breakdowns"]["vertical"] == {"academics": 2}
    assert summary["retrieval_miss_breakdowns"]["gold_source"] == {"gt-example": 2}
    assert summary["top_failed_gold_sources"][0] == {"name": "gt-example", "cases": 3}
    assert summary["dominant_bottleneck"] == "B"
    assert "retrieval/ranking" in summary["next_action"]


def test_write_reports_emits_jsonl_json_and_markdown(tmp_path):
    row = diagnose_case(_case(), _presence(_case()), _retrieval(), _chat())
    summary = summarize_diagnoses([row])

    write_reports(tmp_path, [row], summary)

    assert '"case_id": "gold-001-v7"' in (tmp_path / "latest_cases.jsonl").read_text()
    assert '"dominant_bottleneck": null' in (tmp_path / "latest_summary.json").read_text()
    markdown = (tmp_path / "latest_summary.md").read_text()
    assert "Actual indexed gold corpus coverage" in markdown
    assert "No paid API or network calls" in markdown


def test_parse_args_requires_all_offline_inputs():
    args = parse_args(
        [
            "--manifest",
            "manifest.json",
            "--retrieval-report",
            "retrieval.jsonl",
            "--chat-report",
            "chat.jsonl",
            "--report-dir",
            "reports",
        ]
    )

    assert args.manifest.name == "manifest.json"
    assert args.retrieval_report.name == "retrieval.jsonl"
    assert args.chat_report.name == "chat.jsonl"
    assert args.report_dir.name == "reports"
