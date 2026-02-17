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
