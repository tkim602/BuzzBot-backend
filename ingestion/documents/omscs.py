from __future__ import annotations

from ingestion.documents.discovery import bounded_urls
from ingestion.documents.registry import DocumentSource

_POLICY_PATHS = {
    "/admission-criteria",
    "/degree-requirements",
    "/current-courses",
    "/prospective-student-faqs",
    "/specializations",
    "/cost-and-payment-schedule",
    "/deadlines-decisions-requirements-and-guidelines",
}


def accepts_path(path: str) -> bool:
    return path in _POLICY_PATHS


def discover_urls(source: DocumentSource, body: str) -> tuple[str, ...]:
    return bounded_urls(source, body, accepts_path)
