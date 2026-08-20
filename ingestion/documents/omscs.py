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
}


def discover_urls(source: DocumentSource, body: str) -> tuple[str, ...]:
    return bounded_urls(source, body, _POLICY_PATHS.__contains__)
