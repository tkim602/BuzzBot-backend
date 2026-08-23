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


def test_schedule_follow_up_carries_course_and_term_from_structured_context():
    result = understand_query(
        "Who teaches it?",
        context={
            "intent": "course_schedule",
            "subject": "CS",
            "course_number": "7650",
            "term_code": "202608",
        },
    )

    assert result["intent"] == "course_schedule"
    assert result["subject"] == "CS"
    assert result["course_number"] == "7650"
    assert result["term_code"] == "202608"
    assert result["schedule_query_type"] == "instructors"
    assert result["needs_clarification"] is False


def test_what_about_course_carries_only_the_previous_schedule_term():
    result = understand_query(
        "What about CS 6515?",
        context={
            "intent": "course_schedule",
            "subject": "CS",
            "course_number": "7650",
            "term_code": "202608",
        },
    )

    assert result["intent"] == "course_schedule"
    assert result["course_number"] == "6515"
    assert result["term_code"] == "202608"


def test_schedule_pronoun_without_context_clarifies_instead_of_guessing():
    result = understand_query("Who teaches it?")

    assert result["intent"] == "course_schedule"
    assert result["needs_clarification"] is True
    assert "course code" in result["clarification"].lower()
    assert "term" in result["clarification"].lower()


def test_casual_instructor_wording_keeps_schedule_intent_and_query_type():
    result = understand_query("whos teaching CS 1100 Fall 2026?")

    assert result["intent"] == "course_schedule"
    assert result["subject"] == "CS"
    assert result["course_number"] == "1100"
    assert result["schedule_query_type"] == "instructors"


@pytest.mark.parametrize(
    "query",
    [
        "where does CS7650 meet?",
        "7650 sections Fall 2026?",
        "does 7650 run in Fall 2026?",
        "what CRNs are there for 7650 in Fall 2026?",
    ],
)
def test_incomplete_schedule_phrasing_clarifies_instead_of_guessing(query):
    result = understand_query(query)

    assert result["intent"] == "course_schedule"
    assert result["needs_clarification"] is True


@pytest.mark.parametrize(
    ("query", "query_type"),
    [
        ("What time does it meet?", "meeting"),
        ("Where does that class meet?", "location"),
        ("Which one is online?", "online_availability"),
    ],
)
def test_schedule_follow_up_kinds_use_the_same_bounded_context(query, query_type):
    result = understand_query(
        query,
        context={
            "intent": "course_schedule",
            "subject": "CS",
            "course_number": "7650",
            "term_code": "202608",
        },
    )

    assert result["schedule_query_type"] == query_type
    assert result["course_number"] == "7650"
    assert result["term_code"] == "202608"


def test_what_about_course_without_schedule_context_remains_course_details():
    assert understand_query("What about CS 6515?")["intent"] == "course_details"


def test_term_before_course_code_does_not_become_the_course():
    result = understand_query(
        "What are the Fall 2026 CS 4400 sections, instructor, locations, and meeting times?"
    )

    assert result["intent"] == "course_schedule"
    assert result["subject"] == "CS"
    assert result["course_number"] == "4400"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Is CS 7650 offered in Fall 2026?", "offering"),
        ("What sections does CS 7650 have in Fall 2026?", "sections"),
        ("What are the CRNs for CS 7650 in Fall 2026?", "crns"),
        ("Who teaches CS 7650 in Fall 2026?", "instructors"),
        ("When does CS 7650 meet in Fall 2026?", "meeting"),
        ("Where does CS 7650 meet in Fall 2026?", "location"),
        ("Does CS 7650 have an online section in Fall 2026?", "online_availability"),
        ("Show me CS 7650 for Fall 2026", "general_schedule"),
    ],
)
def test_schedule_query_kind_is_deterministic(query, expected):
    assert understand_query(query)["schedule_query_type"] == expected


@pytest.mark.parametrize(
    "query",
    [
        "GT Fall 2026 tuition and fee payment deadline",
        "Fall 2026 immunization deadlines by last-name group at GT?",
        "fall transfer document deadline at GT?",
    ],
)
def test_domain_deadlines_do_not_route_to_academic_calendar(query):
    assert understand_query(query)["intent"] == "policy"
