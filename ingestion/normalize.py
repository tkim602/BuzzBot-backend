"""URL normalization and content hashing."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse


def normalize_url(url: str) -> str:
    """Normalize a URL: lowercase scheme/host, remove fragment, trailing slash."""
    url = urldefrag(url).url
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    # Remove default ports
    if (scheme == "http" and netloc.endswith(":80")) or (
        scheme == "https" and netloc.endswith(":443")
    ):
        netloc = netloc.rsplit(":", 1)[0]
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))


def content_hash(text: str) -> str:
    """SHA-256 hash of text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_headings(text: str) -> list[str]:
    """Extract lines that look like headings from plain text."""
    headings: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        # Short, title-case-ish lines are likely headings
        if stripped and len(stripped) < 200 and stripped[0].isupper():
            if re.match(r"^[A-Z][^.!?]*$", stripped):
                headings.append(stripped)
    return headings[:20]
