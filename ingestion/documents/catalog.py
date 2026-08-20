from __future__ import annotations

import re

from ingestion.documents.discovery import bounded_urls
from ingestion.documents.registry import DocumentSource

_COURSE_PAGE = re.compile(r"/coursesaz/[a-z0-9-]+$")


def accepts_path(path: str) -> bool:
    return path == "/coursesaz" or _COURSE_PAGE.fullmatch(path) is not None


def discover_urls(source: DocumentSource, body: str) -> tuple[str, ...]:
    return bounded_urls(source, body, accepts_path)
