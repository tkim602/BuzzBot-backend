"""Tests for in-memory TTL cache."""

from __future__ import annotations

import time

from app.core.cache import TTLCache


def test_cache_set_get():
    cache = TTLCache[str](max_size=10, ttl_seconds=10)
    cache.set("k", "v")
    assert cache.get("k") == "v"


def test_cache_expiration():
    cache = TTLCache[str](max_size=10, ttl_seconds=1)
    cache.set("k", "v")
    time.sleep(1.1)
    assert cache.get("k") is None


def test_cache_lru_eviction():
    cache = TTLCache[str](max_size=2, ttl_seconds=10)
    cache.set("a", "1")
    cache.set("b", "2")
    # Touch "a" so "b" becomes least-recently-used.
    assert cache.get("a") == "1"
    cache.set("c", "3")
    assert cache.get("b") is None
    assert cache.get("a") == "1"
    assert cache.get("c") == "3"

