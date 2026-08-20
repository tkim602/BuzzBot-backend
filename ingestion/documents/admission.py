from __future__ import annotations

from ingestion.documents.discovery import bounded_urls
from ingestion.documents.registry import DocumentSource


def _accepts_path(path: str) -> bool:
    parts = path.strip("/").split("/")
    return parts[0] == "first-year" and len(parts) <= 2


def discover_urls(source: DocumentSource, body: str) -> tuple[str, ...]:
    return bounded_urls(source, body, _accepts_path)
