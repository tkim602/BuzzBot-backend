"""Tests for query router."""

from app.rag.router import classify_query


def test_registrar_calendar_intent():
    result = classify_query("When is the registration deadline for fall semester?")
    assert result.intent == "registrar_calendar"
    assert result.source_filter == ["gt-registrar", "gt-calendar-events"]


def test_catalog_course_intent():
    result = classify_query("What are the prerequisites for CS 1332?")
    assert result.intent == "catalog_course"
    assert result.source_filter == ["gt-catalog", "gt-scheduler"]


def test_course_schedule_intent_from_course_code_and_term():
    result = classify_query("Is CS4400 offered in Spring 2025?")
    assert result.intent == "course_schedule_sections"
    assert result.source_filter == ["gt-scheduler", "gt-catalog"]


def test_course_schedule_intent_korean_mixed_query():
    result = classify_query("CS 4400 수업이 2025 Spring에 개설되나요?")
    assert result.intent == "course_schedule_sections"
    assert result.source_filter == ["gt-scheduler", "gt-catalog"]


def test_mixed_calendar_and_course_query_prefers_registrar():
    result = classify_query("When is the registration deadline for CS 4400 Spring 2025?")
    assert result.intent == "registrar_calendar"
    assert result.source_filter == ["gt-registrar", "gt-calendar-events"]


def test_fall_year_not_misclassified_as_course_code():
    result = classify_query("When is the registration deadline for Fall 2026?")
    assert result.intent == "registrar_calendar"
    assert result.source_filter == ["gt-registrar", "gt-calendar-events"]


def test_korean_calendar_query_routes_to_registrar():
    result = classify_query("학사일정 등록 마감일 언제야?")
    assert result.intent == "registrar_calendar"
    assert result.source_filter == ["gt-registrar", "gt-calendar-events"]


def test_library_query_routes_to_library_source():
    result = classify_query("How do I reserve a study room in the library?")
    assert result.source_filter == "gt-library"


def test_course_code_without_schedule_routes_to_catalog():
    result = classify_query("What does MATH 1554 cover?")
    assert result.intent == "catalog_course"
    assert result.source_filter == ["gt-catalog", "gt-scheduler"]


def test_rmp_with_excerpt():
    result = classify_query("What do students think of this professor?", has_rmp_excerpt=True)
    assert result.intent == "rmp_user_provided"


def test_rmp_keyword():
    result = classify_query("rate my professor for Dr. Smith")
    assert result.intent == "rmp_user_provided"


def test_general_intent():
    result = classify_query("Tell me about Georgia Tech")
    assert result.intent == "general"


def test_freshness_for_deadline():
    result = classify_query("When is the deadline for dropping a class this semester?")
    assert result.intent == "registrar_calendar"
    assert result.freshness_strategy == "live_fetch"


def test_indexed_for_stable_catalog():
    result = classify_query("Describe the CS degree program curriculum")
    assert result.intent == "catalog_course"
    assert result.freshness_strategy == "indexed"
