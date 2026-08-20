from __future__ import annotations

from ingestion.documents.discovery import bounded_urls
from ingestion.documents.registry import DocumentSource


def accepts_path(path: str) -> bool:
    return path == "/registration" or path.startswith("/registration/")


def discover_urls(source: DocumentSource, body: str) -> tuple[str, ...]:
    return bounded_urls(source, body, accepts_path)
