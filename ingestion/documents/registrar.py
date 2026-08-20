from __future__ import annotations

from ingestion.documents.discovery import bounded_urls
from ingestion.documents.registry import DocumentSource


def discover_urls(source: DocumentSource, body: str) -> tuple[str, ...]:
    return bounded_urls(
        source,
        body,
        lambda path: path == "/registration" or path.startswith("/registration/"),
    )
