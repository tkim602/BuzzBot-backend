import json
from collections import Counter
from pathlib import Path

import pytest

from eval.quality import runner
from eval.quality.evidence import (
    GoldEvidence,
    evidence_rank,
    load_gold_evidence,
    validate_evidence_texts,
)
from eval.quality.metrics import (
    CaseResult,
    RankedItem,
    first_gold_rank,
    normalize_url,
    reciprocal_rank,
    summarize,
    summarize_evidence,
)
from eval.quality.schema import GoldCase, load_cases, load_manifest_cases


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
        difficulty="direct",
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
                "style": "direct",
            }
        ]
    }
    (tmp_path / "gold_v1.json").write_text(json.dumps(payload), encoding="utf-8")

    cases = load_cases(tmp_path)

    assert len(cases) == 1
    assert cases[0].id == "fixed-001-v1"
    assert cases[0].difficulty == "direct"
    assert cases[0].style == "direct"


def test_verified_dataset_is_fixed_1000_query_benchmark():
    cases = load_cases(Path("eval/quality/data_verified"))

    assert len(cases) == 1000
    assert len({case.variant_group for case in cases}) == 100
    assert len({case.id for case in cases}) == 1000


def test_dev_manifest_selects_one_fixed_case_per_fact():
    cases = load_manifest_cases(Path("eval/quality/manifests/dev_100.json"))

    assert len(cases) == 100
    assert len({case.variant_group for case in cases}) == 100
    assert len({case.id for case in cases}) == 100


def test_change_manifest_selects_two_fixed_cases_per_fact():
    cases = load_manifest_cases(Path("eval/quality/manifests/change_200.json"))

    assert len(cases) == 200
    counts = Counter(case.variant_group for case in cases)
    assert len(counts) == 100
    assert set(counts.values()) == {2}
    assert len({case.id for case in cases}) == 200


def test_change_manifest_contains_all_dev_cases():
    dev = load_manifest_cases(Path("eval/quality/manifests/dev_100.json"))
    change = load_manifest_cases(Path("eval/quality/manifests/change_200.json"))

    assert {case.id for case in dev} <= {case.id for case in change}


def test_schedule_manifest_covers_five_distinct_structured_sql_cases():
    cases = load_manifest_cases(Path("eval/quality/manifests/schedule_5.json"))

    assert len(cases) == 5
    assert len({case.variant_group for case in cases}) == 5
    assert {case.question_type for case in cases} == {"course_schedule"}
    assert all(case.time_sensitive for case in cases)


def test_user_20_manifest_uses_unseen_facts_and_urls():
    user_cases = load_manifest_cases(Path("eval/quality/manifests/user_20.json"))
    dev_cases = load_manifest_cases(Path("eval/quality/manifests/dev_100.json"))

    assert len(user_cases) == 20
    assert len({case.variant_group for case in user_cases}) == 20
    assert len({case.id for case in user_cases}) == 20
    assert {case.variant_group for case in user_cases}.isdisjoint(
        case.variant_group for case in dev_cases
    )
    assert {url.rstrip("/") for case in user_cases for url in case.gold_urls}.isdisjoint(
        url.rstrip("/") for case in dev_cases for url in case.gold_urls
    )


def test_user_holdout_10_is_disjoint_from_both_development_sets():
    holdout = load_manifest_cases(Path("eval/quality/manifests/user_holdout_10.json"))
    development = [
        *load_manifest_cases(Path("eval/quality/manifests/dev_100.json")),
        *load_manifest_cases(Path("eval/quality/manifests/user_20.json")),
    ]

    assert len(holdout) == 10
    assert len({case.variant_group for case in holdout}) == 10
    assert {case.variant_group for case in holdout}.isdisjoint(
        case.variant_group for case in development
    )
    assert {url.rstrip("/") for case in holdout for url in case.gold_urls}.isdisjoint(
        url.rstrip("/") for case in development for url in case.gold_urls
    )


def test_dev_evidence_artifact_covers_every_fixed_fact():
    cases = load_manifest_cases(Path("eval/quality/manifests/dev_100.json"))
    evidence = load_gold_evidence(Path("eval/quality/gold_evidence/dev_100.json"), cases)

    assert len(evidence) == 100
    assert set(evidence) == {case.variant_group for case in cases}


def test_manifest_fails_when_a_case_id_is_unknown(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "cases.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "gold-001-v1",
                        "variant_group": "gold-001",
                        "question": "Question?",
                        "gold_answer": "Answer.",
                        "gold_urls": ["https://example.gatech.edu/rule"],
                        "gold_sources": ["gt-example"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "broken",
                "master_dataset": "dataset",
                "case_ids": ["gold-001-v2"],
                "expected_fact_count": 1,
                "cases_per_fact": 1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown case ids"):
        load_manifest_cases(manifest)


def test_production_lift_is_positive_when_production_beats_raw():
    summaries = {
        "production": {"hit_at_5": 0.443},
        "raw": {"hit_at_5": 0.328},
    }

    assert hasattr(runner, "_production_lift_at_5")
    assert runner._production_lift_at_5(summaries) == pytest.approx(0.115)


def test_runner_uses_manifest_cases_when_requested(monkeypatch, tmp_path):
    selected = [_case()]
    monkeypatch.setattr(runner, "load_manifest_cases", lambda path: selected)
    monkeypatch.setattr(runner, "load_cases", lambda path: pytest.fail("master loader used"))

    assert runner._evaluation_cases(tmp_path / "master", tmp_path / "manifest.json") == selected


def _result(mode: str, rank: int | None) -> CaseResult:
    return CaseResult(_case(), mode, rank, rank, rank, 5, 1.0, ())


def test_diagnose_distinguishes_production_from_ablation_failures():
    production = _result("production", 1)
    results = {
        "production": [production],
        "raw": [_result("raw", None)],
        "vector": [_result("vector", None)],
        "fts": [_result("fts", None)],
    }

    runner._diagnose(results)

    assert "ALL_ABLATIONS_MISS" in production.failure_tags
    assert "PRODUCTION_RECOVERS_ABLATIONS" in production.failure_tags
    assert "PRODUCTION_MISS" not in production.failure_tags
    assert "ALL_METHODS_FAIL" not in production.failure_tags


def test_diagnose_marks_only_actual_production_misses():
    production = _result("production", None)
    results = {
        "production": [production],
        "raw": [_result("raw", None)],
        "vector": [_result("vector", None)],
        "fts": [_result("fts", None)],
    }

    runner._diagnose(results)

    assert "PRODUCTION_MISS" in production.failure_tags


def test_gold_evidence_requires_complete_manifest_and_gold_url(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "facts": {"gold-001": {"url": "https://example.edu", "span": "policy"}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="outside gold URLs"):
        load_gold_evidence(path, [_case()])

    path.write_text(json.dumps({"version": 1, "facts": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="fact ids"):
        load_gold_evidence(path, [_case()])


def test_gold_evidence_must_exist_in_indexed_document():
    evidence = {
        "gold-001": GoldEvidence(
            "gold-001",
            "https://registrar.gatech.edu/current-students/transcripts",
            "official transcript service",
        )
    }
    with pytest.raises(ValueError, match="not present"):
        validate_evidence_texts(evidence, {evidence["gold-001"].url: "unrelated text"})


def test_gold_evidence_allows_explicit_missing_corpus_fact(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "facts": {
                    "gold-001": {
                        "url": _case().gold_urls[0],
                        "status": "CORPUS_EVIDENCE_MISSING",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = load_gold_evidence(path, [_case()])

    assert loaded["gold-001"].span is None
    assert evidence_rank(loaded["gold-001"], ()) is None


def test_evidence_rank_matches_exact_normalized_span_and_reports_metrics():
    gold = GoldEvidence("gold-001", _case().gold_urls[0], "official transcript service")
    items = (
        RankedItem("https://example.edu", "other", "academics", text="official transcript service"),
        RankedItem(gold.url + "/", "source", "academics", text="Official\n transcript service"),
    )
    result = CaseResult(_case(), "production", 2, 2, 2, 2, 1.0, items, evidence_rank=2)

    assert evidence_rank(gold, items) == 2
    assert summarize_evidence([result])["evidence_hit_at_3"] == 1.0


def test_coverage_summary_separates_documents_from_evidence():
    evidence = {
        "gold-001": GoldEvidence("gold-001", _case().gold_urls[0], "official policy"),
        "gold-002": GoldEvidence("gold-002", _case().gold_urls[0], None),
    }

    assert runner._coverage_summary({"document_coverage": 1.0}, evidence) == {
        "document_coverage": 1.0,
        "evidence_coverage": 0.5,
    }


def test_dev_100_retrieval_baseline_freezes_the_explained_delta():
    baseline = json.loads(
        Path("eval/quality/baselines/dev_100_retrieval.json").read_text(encoding="utf-8")
    )

    assert baseline["manifest_sha256"] == (
        "58c343d37902a4bbbf4f281509413b701d55cca6cbeab8001404111de6c59562"
    )
    assert baseline["before"]["hit_at_5"] == 0.42
    assert baseline["after"]["hit_at_5"] == 0.57
    assert baseline["case_delta"] == {"wins": 17, "regressions": 2, "net": 15}
    assert baseline["after_failure_boundary"] == {
        "gold_not_returned": 37,
        "rank_gt_5": 6,
    }


def test_user_20_routing_baseline_is_bound_to_frozen_manifest():
    baseline = json.loads(
        Path("eval/quality/baselines/user_20_routing.json").read_text(encoding="utf-8")
    )

    assert baseline["manifest_sha256"] == (
        "b566cfe61a7913a1a5eeb2e58613421d2e717db99fef87b5c30686c61a1e26e1"
    )
    assert baseline["before"]["correct_and_supported"] == 11
    assert baseline["after"]["correct_and_supported"] == 16
    assert baseline["case_delta"] == {"wins": 5, "regressions": 0, "net": 5}


def test_user_holdout_baseline_preserves_blind_result_and_scoped_follow_up():
    baseline = json.loads(
        Path("eval/quality/baselines/user_holdout_10.json").read_text(encoding="utf-8")
    )

    assert baseline["manifest_sha256"] == (
        "f0906d09440167b5f364eef621ed3ace2bda05783ce64cb182fcf0209c35e5b5"
    )
    failures = set(baseline["blind_run"]["failed_case_ids"])
    assert baseline["blind_run"]["correct_and_supported"] == 7
    assert set(baseline["focused_verification"]["passed_case_ids"]) == failures
    assert baseline["focused_verification"]["full_manifest_rerun"] is False


def test_gold_not_returned_diagnosis_selects_one_largest_bucket():
    diagnosis = json.loads(
        Path("eval/quality/baselines/dev_100_gold_not_returned_diagnosis.json").read_text(
            encoding="utf-8"
        )
    )
    buckets = diagnosis["buckets"]

    assert sum(bucket["count"] for bucket in buckets.values()) == 37
    assert len({case_id for bucket in buckets.values() for case_id in bucket["case_ids"]}) == 37
    assert diagnosis["largest_bucket"] == {
        "name": "LEXICAL_OR_RECOVERABLE_DEEP",
        "count": 20,
    }
    assert diagnosis["fix_applied"] is False
