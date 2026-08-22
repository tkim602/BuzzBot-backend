import json
from pathlib import Path

import pytest

from eval.quality import runner
from eval.quality.metrics import (
    CaseResult,
    RankedItem,
    first_gold_rank,
    normalize_url,
    reciprocal_rank,
    summarize,
)
from eval.quality.schema import GoldCase, load_cases


def _case(case_id: str = "gold-001-v1", group: str = "gold-001") -> GoldCase:
    return GoldCase(
        id=case_id,
        variant_group=group,
        question="How do I order a transcript?",
        gold_answer="Use the official transcript service.",
        gold_urls=("https://registrar.gatech.edu/current-students/transcripts/",),
        gold_sources=("gt-registrar-lifecycle",),
        gold_vertical="academics",
        gold_locator="official transcript",
        question_type="process",
        time_sensitive=False,
        style="direct",
    )


def test_normalize_url_ignores_query_fragment_and_trailing_slash():
    assert normalize_url("HTTPS://Registrar.Gatech.edu/current-students/transcripts/?x=1#top") == (
        "https://registrar.gatech.edu/current-students/transcripts"
    )


def test_first_gold_rank_uses_normalized_canonical_url():
    case = _case()
    items = [
        RankedItem("https://example.gatech.edu/a", "other", "academics"),
        RankedItem(
            "https://registrar.gatech.edu/current-students/transcripts",
            "gt-registrar-lifecycle",
            "academics",
        ),
    ]
    assert first_gold_rank(case, items) == 2
    assert reciprocal_rank(2) == 0.5


def test_summarize_reports_question_and_fact_robustness():
    case_a = _case("gold-001-v1", "gold-001")
    case_b = _case("gold-001-v2", "gold-001")
    case_c = _case("gold-002-v1", "gold-002")
    results = [
        CaseResult(case_a, "production", 1, 1, 1, 5, 10.0, ()),
        CaseResult(case_b, "production", None, None, None, 5, 20.0, ()),
        CaseResult(case_c, "production", 3, 2, 1, 5, 30.0, ()),
    ]
    summary = summarize(results)
    assert summary["hit_at_1"] == 1 / 3
    assert summary["hit_at_5"] == 2 / 3
    assert summary["fact_macro_hit_at_5"] == 0.75
    assert summary["all_variants_hit_at_5"] == 0.5


def test_load_cases_reads_fixed_query_level_json_shards(tmp_path):
    payload = {
        "items": [
            {
                "id": "fixed-001-v1",
                "variant_group": "fixed-001",
                "question": "What is the official rule?",
                "gold_answer": "The official answer.",
                "gold_urls": ["https://example.gatech.edu/rule"],
                "gold_sources": ["gt-example"],
                "gold_vertical": "academics",
                "gold_locator": "official rule",
                "question_type": "policy",
                "time_sensitive": False,
                "difficulty": "direct",
            }
        ]
    }
    (tmp_path / "gold_v1.json").write_text(json.dumps(payload), encoding="utf-8")

    cases = load_cases(tmp_path)

    assert len(cases) == 1
    assert cases[0].id == "fixed-001-v1"
    assert cases[0].style == "direct"


def test_verified_dataset_is_fixed_1000_query_benchmark():
    cases = load_cases(Path("eval/quality/data_verified"))

    assert len(cases) == 1000
    assert len({case.variant_group for case in cases}) == 100
    assert len({case.id for case in cases}) == 1000


def test_production_lift_is_positive_when_production_beats_raw():
    summaries = {
        "production": {"hit_at_5": 0.443},
        "raw": {"hit_at_5": 0.328},
    }

    assert hasattr(runner, "_production_lift_at_5")
    assert runner._production_lift_at_5(summaries) == pytest.approx(0.115)
