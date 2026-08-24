from pathlib import Path

from eval.langsmith.run_policy_answer import TAXONOMY_LABELS, load_snapshot, load_taxonomy
from eval.quality.schema import load_manifest_cases

SNAPSHOT = Path("eval/frozen/policy_answer_dev_100_v1/snapshot.json")
TAXONOMY = Path("eval/frozen/policy_answer_dev_100_v1/taxonomy.json")
MANIFEST = Path("eval/quality/manifests/dev_100.json")


def test_policy_snapshot_freezes_exact_dev_100_top_five_evidence():
    snapshot = load_snapshot(SNAPSHOT)
    expected_ids = {case.id for case in load_manifest_cases(MANIFEST)}

    assert snapshot.provenance["retrieval_report_sha256"] == (
        "30813af5e1830f9eb99dc8948bc2e5316e9650f4c6d04aba2b891190ab27da59"
    )
    assert snapshot.provenance["baseline_git_sha"] == (
        "b6a44bc435bc02202566b44063494146d52ea4c0"
    )
    assert len(snapshot.cases) == 100
    assert {case.case_id for case in snapshot.cases} == expected_ids
    assert all(len(case.evidence) <= 5 for case in snapshot.cases)
    assert all(
        {"url", "source_name", "vertical", "method", "text"} <= set(item)
        for case in snapshot.cases
        for item in case.evidence
    )


def test_policy_taxonomy_covers_exactly_the_twenty_one_answer_layer_failures():
    rows = load_taxonomy(TAXONOMY)

    assert len(rows) == 21
    assert len({row.case_id for row in rows}) == 21
    assert {row.category for row in rows} <= TAXONOMY_LABELS
    assert all(row.rationale.strip() for row in rows)

