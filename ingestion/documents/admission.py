from __future__ import annotations

import re

from ingestion.documents.discovery import bounded_urls
from ingestion.documents.registry import DocumentSource

_FIRST_YEAR_PAGE = re.compile(r"/first-year(?:/[a-z0-9-]+)?$")


def accepts_path(path: str) -> bool:
    return _FIRST_YEAR_PAGE.fullmatch(path) is not None


def discover_urls(source: DocumentSource, body: str) -> tuple[str, ...]:
    return bounded_urls(source, body, accepts_path)
