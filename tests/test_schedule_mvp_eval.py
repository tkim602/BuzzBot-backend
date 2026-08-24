from pathlib import Path

from eval.frozen.schedule_mvp_v1.runner import (
    EvalCase,
    load_cases,
    score_nlu,
    score_renderer_result,
)

MANIFEST = Path("eval/frozen/schedule_mvp_v1/manifest.json")


def test_schedule_mvp_manifest_freezes_renderer_and_nlu_case_counts():
    renderer, nlu = load_cases(MANIFEST)

    assert len(renderer) == 140
    assert len(nlu) == 150
    assert len({case.case_id for case in renderer}) == 140
    assert len({case.case_id for case in nlu}) == 150


def test_schedule_nlu_frozen_suite_meets_mvp_gate():
    _, cases = load_cases(MANIFEST)

    summary = score_nlu(cases)

    assert summary["success_rate"] >= 0.85
    assert summary["unsafe_guess_rate"] == 0
    assert summary["clarification_accuracy"] == 1


def test_schedule_nlu_handles_realistic_instructor_and_incomplete_course_phrasing():
    _, cases = load_cases(MANIFEST)
    by_id = {case.case_id: case for case in cases}

    assert score_nlu([by_id["nlu-CS-1100-instructors"]])["passed"] == 1
    assert score_nlu([by_id["nlu-missing-subject-offering"]])["passed"] == 1


def test_schedule_renderer_score_requires_exact_typed_facts_and_safe_wording():
    case = EvalCase(
        case_id="renderer-CS-1100-online",
        question="is there an online section for CS1100 in Fall 2026?",
        subject="CS",
        course_number="1100",
        term_code="202608",
        query_type="online_availability",
    )
    expected = {
        "sections": [
            {
                "crn": "12345",
                "section_code": "O01",
                "campus": "Online Campus",
                "schedule_type": "Lecture",
                "instructional_method": "Online",
                "instructors": ["Ada Lovelace"],
                "notes": None,
                "meetings": [],
            }
        ]
    }
    result = {
        "intent": "course_schedule",
        "subject": "CS",
        "course_number": "1100",
        "term_code": "202608",
        "schedule_query_type": "online_availability",
        "answer": "Yes. CS 1100 has an online section listed in Fall 2026: O01.",
        "answer_valid": True,
        "citations": [{"url": "https://oscar.gatech.edu/schedule"}],
        "evidence": [
            {
                "url": "https://oscar.gatech.edu/schedule",
                "metadata": {
                    **expected["sections"][0],
                    "subject": "CS",
                    "course_number": "1100",
                    "term_code": "202608",
                    "data_version_id": "version-1",
                },
            }
        ],
    }

    assert score_renderer_result(case, result, expected, "version-1") is True
    result["answer"] = "Yes, the online section is open for registration."
    assert score_renderer_result(case, result, expected, "version-1") is False
