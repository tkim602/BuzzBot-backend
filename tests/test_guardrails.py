"""Tests for chat request guardrails."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.core.config import settings
from app.core.guardrails import GuardrailViolation, SlidingWindowLimiter, normalize_query


@contextmanager
def _override(**kwargs):
    old = {k: getattr(settings, k) for k in kwargs}
    try:
        for k, v in kwargs.items():
            setattr(settings, k, v)
        yield
    finally:
        for k, v in old.items():
            setattr(settings, k, v)


def test_normalize_query():
    assert normalize_query("  CS   4400   Spring 2025  ") == "cs 4400 spring 2025"


def test_duplicate_query_cooldown():
    limiter = SlidingWindowLimiter()
    with _override(
        chat_min_interval_seconds=0.0,
        chat_duplicate_cooldown_seconds=60,
        chat_rate_limit_per_minute=100,
        chat_rate_limit_per_hour=1000,
        chat_rate_limit_per_day=5000,
    ):
        limiter.enforce("c1", "cs 4400 spring 2025")
        with pytest.raises(GuardrailViolation):
            limiter.enforce("c1", "cs 4400 spring 2025")


def test_per_minute_limit():
    limiter = SlidingWindowLimiter()
    with _override(
        chat_min_interval_seconds=0.0,
        chat_duplicate_cooldown_seconds=0,
        chat_rate_limit_per_minute=2,
        chat_rate_limit_per_hour=1000,
        chat_rate_limit_per_day=5000,
    ):
        limiter.enforce("c2", "q1")
        limiter.enforce("c2", "q2")
        with pytest.raises(GuardrailViolation):
            limiter.enforce("c2", "q3")

