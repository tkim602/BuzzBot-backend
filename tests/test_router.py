"""Tests for query router."""

from app.rag.router import classify_query


def test_registrar_calendar_intent():
    result = classify_query("When is the registration deadline for fall semester?")
    assert result.intent == "registrar_calendar"
    assert result.source_filter == "gt-registrar"


def test_catalog_course_intent():
    result = classify_query("What are the prerequisites for CS 1332?")
    assert result.intent == "catalog_course"
    assert result.source_filter == "gt-catalog"


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
