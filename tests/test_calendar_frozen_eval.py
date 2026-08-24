from pathlib import Path

from app.retrieval.documents import DocumentEvidence
from eval.frozen.academic_calendar_20_v1.runner import load_cases, target_event_rank

MANIFEST = Path("eval/frozen/academic_calendar_20_v1/manifest.json")


def test_calendar_manifest_contains_twenty_source_consistent_events():
    cases = load_cases(MANIFEST)

    assert len(cases) == 20
    assert len({case.case_id for case in cases}) == 20
    assert {case.gold_source for case in cases} == {"gt-academic-calendar"}
    assert {case.gold_url for case in cases} == {
        "https://registrar.gatech.edu/current-academic-calendar"
    }


def test_calendar_rank_requires_the_exact_frozen_event():
    evidence = [
        DocumentEvidence(
            chunk_id="wrong",
            text="Georgia Tech Academic Calendar — Event 454 | First day of classes",
            title="Academic Calendar",
            canonical_url="https://registrar.gatech.edu/current-academic-calendar",
            source_name="gt-academic-calendar",
            source_type="academic_calendar",
            authority="academic_calendar",
            fetched_at=None,
            edition="2026-2027",
            score=1,
            retrieval_method="hybrid_rrf",
        ),
        DocumentEvidence(
            chunk_id="right",
            text="Georgia Tech Academic Calendar — Event 473 | Final exams",
            title="Academic Calendar",
            canonical_url="https://registrar.gatech.edu/current-academic-calendar",
            source_name="gt-academic-calendar",
            source_type="academic_calendar",
            authority="academic_calendar",
            fetched_at=None,
            edition="2026-2027",
            score=0.9,
            retrieval_method="hybrid_rrf",
        ),
    ]

    assert target_event_rank(473, evidence) == 2
    assert target_event_rank(999, evidence) is None
