import json
from datetime import date, time
from pathlib import Path
from types import SimpleNamespace

MANIFEST = Path("eval/frozen/schedule_sql_150_v1/manifest.json")


def test_frozen_schedule_manifest_preserves_the_verified_snapshot():
    payload = json.loads(MANIFEST.read_text())
    cases = payload["items"]

    assert len(cases) == 150
    assert {case["question_type"] for case in cases} == {
        "offering",
        "sections_crn",
        "instructor_meeting",
    }
    assert {case["snapshot"]["data_version_id"] for case in cases} == {
        "bf5473f7-5d3f-4b3d-a2ba-eb8b84a54a60"
    }
    assert {case["term_code"] for case in cases} == {"202608"}


def _case(question_type: str) -> dict:
    payload = json.loads(MANIFEST.read_text())
    return next(case for case in payload["items"] if case["question_type"] == question_type)


def _offerings(case: dict) -> list[SimpleNamespace]:
    expected = case["expected"]
    if case["question_type"] == "offering":
        return [
            SimpleNamespace(
                subject=expected["subject"],
                course_number=expected["course_number"],
                title=expected["title"],
                credits=expected["credits"],
            )
            for _ in range(expected["section_count"])
        ]

    return [
        SimpleNamespace(
            **{key: value for key, value in section.items() if key != "meetings"},
            meetings=tuple(
                SimpleNamespace(
                    **{
                        **meeting,
                        "start_time": time.fromisoformat(meeting["start_time"])
                        if meeting["start_time"]
                        else None,
                        "end_time": time.fromisoformat(meeting["end_time"])
                        if meeting["end_time"]
                        else None,
                        "start_date": date.fromisoformat(meeting["start_date"]),
                        "end_date": date.fromisoformat(meeting["end_date"]),
                    }
                )
                for meeting in section.get("meetings", [])
            ),
        )
        for section in expected["sections"]
    ]


def test_frozen_schedule_scorer_checks_typed_retrieval_not_answer_text():
    from eval.frozen.schedule_sql_150_v1.runner import score_case

    for question_type in ("offering", "sections_crn", "instructor_meeting"):
        case = _case(question_type)
        assert score_case(case, _offerings(case)) is True

    bad = _offerings(_case("instructor_meeting"))
    bad[0].instructors = ("Wrong Instructor",)
    assert score_case(_case("instructor_meeting"), bad) is False
