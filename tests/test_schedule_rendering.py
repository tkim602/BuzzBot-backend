from app.graph.state import EvidenceItem


def _section(
    section_code: str,
    crn: str,
    campus: str,
    *,
    instructors: list[str],
    meetings: list[dict[str, object]],
    instructional_method: str | None = None,
) -> EvidenceItem:
    text = f"CS 7650 Natural Language; section {section_code}; CRN {crn}; {campus}."
    return EvidenceItem(
        kind="schedule",
        text=text,
        url="https://oscar.gatech.edu/schedule",
        title="CS 7650 schedule",
        fetched_at="2026-08-20T00:00:00+00:00",
        source="oscar",
        metadata={
            "term_code": "202608",
            "subject": "CS",
            "course_number": "7650",
            "title": "Natural Language",
            "section_code": section_code,
            "crn": crn,
            "campus": campus,
            "schedule_type": "Lecture",
            "instructional_method": instructional_method,
            "instructors": instructors,
            "notes": None,
            "meetings": meetings,
            "source_url": "https://oscar.gatech.edu/schedule",
            "data_version_id": "version-1",
            "freshness": "CURRENT",
            "data_as_of": "2026-08-20T00:00:00+00:00",
        },
    )


TIMED = {
    "meeting_type": "Class",
    "days": "MW",
    "start_time": "15:30",
    "end_time": "16:45",
    "start_date": "2026-08-24",
    "end_date": "2026-12-17",
    "building": "Paper Tricentennial",
    "room": "109",
    "is_tba": False,
}


def _evidence() -> list[EvidenceItem]:
    return [
        _section(
            "A",
            "90427",
            "Georgia Tech-Atlanta * Campus",
            instructors=["Kartik Goyal"],
            meetings=[TIMED],
            instructional_method="In Person",
        ),
        _section(
            "O01",
            "89627",
            "Online Campus",
            instructors=["Mark Riedl"],
            meetings=[],
            instructional_method="Online",
        ),
    ]


def test_offering_answer_is_conversational_without_raw_row_dump():
    from app.graph.schedule_rendering import render_schedule_answer

    answer, citations = render_schedule_answer("offering", _evidence())

    assert answer.startswith("Yes. CS 7650 (Natural Language) is offered in Fall 2026")
    assert "A (Atlanta)" in answer
    assert "O01 (Online)" in answer
    assert "CRN" not in answer
    assert ";" not in answer
    assert [citation["quote"] for citation in citations] == [item["text"] for item in _evidence()]


def test_schedule_renderers_include_only_question_relevant_typed_facts():
    from app.graph.schedule_rendering import render_schedule_answer

    sections, _ = render_schedule_answer("sections", _evidence())
    instructors, _ = render_schedule_answer("instructors", _evidence())
    meetings, _ = render_schedule_answer("meeting", _evidence())

    assert "A — CRN 90427 — Atlanta" in sections
    assert "O01 — CRN 89627 — Online" in sections
    assert "A — Kartik Goyal" in instructors
    assert "O01 — Mark Riedl" in instructors
    assert "A — MW, 3:30 PM–4:45 PM — Paper Tricentennial 109" in meetings
    assert "O01 — meeting time TBA" in meetings


def test_online_answer_does_not_claim_live_seat_availability():
    from app.graph.schedule_rendering import render_schedule_answer

    answer, _ = render_schedule_answer("online_availability", _evidence())

    assert answer == "Yes. CS 7650 has an online section listed in Fall 2026: O01."
    assert all(word not in answer.lower() for word in ("seat", "open", "register"))


def test_location_and_general_answers_remain_readable():
    from app.graph.schedule_rendering import render_schedule_answer

    locations, _ = render_schedule_answer("location", _evidence())
    general, _ = render_schedule_answer("general_schedule", _evidence())

    assert "A — Paper Tricentennial 109" in locations
    assert "O01 — location TBA" in locations
    assert "A — CRN 90427 — Atlanta — Kartik Goyal" in general
    assert "O01 — CRN 89627 — Online — Mark Riedl — meeting time TBA" in general
    assert ";" not in general


def test_schedule_validation_rejects_any_changed_rendered_fact():
    from app.graph.schedule_rendering import render_schedule_answer, validate_schedule_answer

    answer, citations = render_schedule_answer("meeting", _evidence())

    assert validate_schedule_answer("meeting", _evidence(), answer, citations) is True
    assert (
        validate_schedule_answer(
            "meeting", _evidence(), answer.replace("3:30 PM", "2:30 PM"), citations
        )
        is False
    )
