"""Tests for URL normalization and content hashing."""

from ingestion.normalize import content_hash, normalize_url


def test_normalize_removes_fragment():
    assert normalize_url("https://example.com/page#section") == "https://example.com/page"


def test_normalize_removes_trailing_slash():
    assert normalize_url("https://example.com/page/") == "https://example.com/page"


def test_normalize_trims_surrounding_whitespace():
    assert normalize_url("  https://example.com/page/  ") == "https://example.com/page"


def test_normalize_lowercases_scheme_and_host():
    assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"


def test_normalize_removes_default_port_https():
    assert normalize_url("https://example.com:443/page") == "https://example.com/page"


def test_normalize_removes_default_port_http():
    assert normalize_url("http://example.com:80/page") == "http://example.com/page"


def test_normalize_keeps_non_default_port():
    assert normalize_url("https://example.com:8080/page") == "https://example.com:8080/page"


def test_normalize_root_path():
    assert normalize_url("https://example.com") == "https://example.com/"


def test_content_hash_deterministic():
    h1 = content_hash("hello world")
    h2 = content_hash("hello world")
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_content_hash_different():
    assert content_hash("hello") != content_hash("world")
