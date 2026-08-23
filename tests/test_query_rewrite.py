"""Tests for query rewriting and temporal enrichment."""

from app.core.config import settings
from app.rag.query_rewrite import rewrite_query


async def test_rewrite_adds_current_term_for_date_sensitive(monkeypatch):
    monkeypatch.setattr(settings, "rag_enable_query_rewrite", True)
    monkeypatch.setattr(settings, "rag_query_rewrite_mode", "rule")

    result = await rewrite_query("When is the registration deadline?")

    assert result.date_sensitive is True
    assert result.current_term in result.rewritten_query


async def test_rewrite_resolves_pronoun_from_history(monkeypatch):
    monkeypatch.setattr(settings, "rag_enable_query_rewrite", True)
    monkeypatch.setattr(settings, "rag_query_rewrite_mode", "rule")

    history = [
        {"role": "user", "content": "Tell me about CS 4400."},
        {"role": "assistant", "content": "CS 4400 is a database course."},
    ]

    result = await rewrite_query("Is it offered in Spring 2025?", history=history)

    assert "CS 4400" in result.rewritten_query
    assert result.detected_course_code == "CS 4400"


async def test_rewrite_keeps_explicit_term(monkeypatch):
    monkeypatch.setattr(settings, "rag_enable_query_rewrite", True)
    monkeypatch.setattr(settings, "rag_query_rewrite_mode", "rule")

    result = await rewrite_query("What is the add/drop deadline for Fall 2026?")

    assert "Fall 2026" in result.rewritten_query
    assert result.detected_term_name == "Fall 2026"


async def test_rewrite_detects_course_code_after_explicit_term(monkeypatch):
    monkeypatch.setattr(settings, "rag_enable_query_rewrite", True)
    monkeypatch.setattr(settings, "rag_query_rewrite_mode", "rule")

    result = await rewrite_query("What are the Fall 2026 CS 4400 sections?")

    assert result.detected_course_code == "CS 4400"


async def test_rewrite_does_not_treat_fall_year_as_course_code(monkeypatch):
    monkeypatch.setattr(settings, "rag_enable_query_rewrite", True)
    monkeypatch.setattr(settings, "rag_query_rewrite_mode", "rule")

    history = [
        {"role": "user", "content": "when is the application deadline for Fall 2026"},
        {"role": "assistant", "content": "I could not find the exact date yet."},
    ]

    result = await rewrite_query("isnt it Mar 1?", history=history)

    assert "FALL 2026" not in result.rewritten_query


async def test_rewrite_does_not_append_current_term_for_admissions_deadline(monkeypatch):
    monkeypatch.setattr(settings, "rag_enable_query_rewrite", True)
    monkeypatch.setattr(settings, "rag_query_rewrite_mode", "rule")

    result = await rewrite_query("application deadline for MSCS")

    assert "Spring" not in result.rewritten_query
    assert "Fall" not in result.rewritten_query
