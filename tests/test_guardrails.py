"""Tests for chat request guardrails."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from starlette.requests import Request

from app.core.auth import RequestIdentity
from app.core.config import settings
from app.core.guardrails import (
    GuardrailViolation,
    SlidingWindowLimiter,
    get_client_fingerprint,
    normalize_query,
)


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


def test_authenticated_identity_uses_verified_uid_only():
    request = _request("203.0.113.4", {"user-agent": "browser"})

    assert get_client_fingerprint(request, RequestIdentity(uid="uid-1")) == "firebase:uid-1"
    assert get_client_fingerprint(request, RequestIdentity(uid="uid-2")) == "firebase:uid-2"
    assert get_client_fingerprint(request, RequestIdentity(uid="uid-1")) == "firebase:uid-1"


def test_anonymous_identity_uses_direct_client_when_proxy_is_untrusted():
    request = _request(
        "203.0.113.4",
        {"user-agent": "browser", "x-forwarded-for": "198.51.100.9"},
    )

    direct = get_client_fingerprint(request, RequestIdentity(), trust_proxy_headers=False)
    without_spoof = get_client_fingerprint(
        _request("203.0.113.4", {"user-agent": "browser"}),
        RequestIdentity(),
        trust_proxy_headers=False,
    )
    assert direct == without_spoof


def test_trusted_proxy_accepts_valid_chain_and_rejects_malformed_chain():
    forwarded = _request(
        "10.0.0.2",
        {"user-agent": "browser", "x-forwarded-for": "198.51.100.9, 10.0.0.1"},
    )
    direct_client = _request("198.51.100.9", {"user-agent": "browser"})
    assert get_client_fingerprint(
        forwarded, RequestIdentity(), trust_proxy_headers=True
    ) == get_client_fingerprint(direct_client, RequestIdentity(), trust_proxy_headers=False)

    malformed = _request(
        "10.0.0.2",
        {"user-agent": "browser", "x-forwarded-for": "attacker, 10.0.0.1"},
    )
    assert get_client_fingerprint(
        malformed, RequestIdentity(), trust_proxy_headers=True
    ) == get_client_fingerprint(
        _request("10.0.0.2", {"user-agent": "browser"}),
        RequestIdentity(),
        trust_proxy_headers=False,
    )


def _request(host: str, headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/chat",
            "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
            "client": (host, 1234),
        }
    )
