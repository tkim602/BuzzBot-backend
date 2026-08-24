from pathlib import Path

from eval.langsmith.run_calendar_answer import load_calendar_snapshot


def test_calendar_answer_snapshot_reuses_the_twenty_frozen_official_events():
    snapshot = load_calendar_snapshot(Path("eval/frozen/academic_calendar_20_v1/manifest.json"))

    assert len(snapshot.cases) == 20
    assert len({case.case_id for case in snapshot.cases}) == 20
    assert all(case.metadata["document_hit_at_5"] for case in snapshot.cases)
    assert all(case.metadata["evidence_hit_at_5"] for case in snapshot.cases)
    assert all(len(case.evidence) == 1 for case in snapshot.cases)
    assert all(case.evidence[0]["text"] == case.gold_answer for case in snapshot.cases)
    assert {case.evidence[0]["vertical"] for case in snapshot.cases} == {"calendar"}
