import pytest

from app.graph.understanding import understand_query


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "Is CS 7650 offered in Fall 2026?",
            {
                "intent": "course_schedule",
                "subject": "CS",
                "course_number": "7650",
                "term_code": "202608",
                "needs_clarification": False,
            },
        ),
        (
            "What are the prerequisites for cs-6515?",
            {
                "intent": "course_details",
                "subject": "CS",
                "course_number": "6515",
                "needs_clarification": False,
            },
        ),
        (
            "When is the Spring 2027 registration deadline?",
            {
                "intent": "registration_calendar",
                "term_code": "202702",
                "needs_clarification": False,
            },
        ),
        (
            "What documents are required for OMSCS admission?",
            {"intent": "policy", "needs_clarification": False},
        ),
    ],
)
def test_understanding_routes_common_gt_questions(query, expected):
    result = understand_query(query)

    assert result | expected == result


def test_schedule_query_requires_explicit_course_and_term():
    result = understand_query("When is CS 7650 offered?")

    assert result["intent"] == "course_schedule"
    assert result["needs_clarification"] is True
    assert "term" in result["clarification"].lower()


def test_user_term_supplies_missing_schedule_term():
    result = understand_query("What sections does CS 7650 have?", user_term="Summer 2027")

    assert result["term_code"] == "202705"
    assert result["needs_clarification"] is False
