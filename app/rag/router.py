"""Query router — classify intent and decide freshness strategy."""

from __future__ import annotations

import re
from dataclasses import dataclass

CALENDAR_KEYWORDS = [
    "deadline", "registration", "calendar", "semester", "drop", "add",
    "withdrawal", "final exam", "commencement", "holiday", "break",
    "spring", "summer", "fall", "midterm", "schedule",
]
CATALOG_KEYWORDS = [
    "course", "class", "credit", "prerequisite", "degree", "major",
    "minor", "program", "curriculum", "syllabus", "description",
    "catalog", "department", "school",
]
RMP_KEYWORDS = [
    "rate my professor", "ratemyprofessor", "rmp", "professor rating",
    "professor review", "teaching quality",
]
FRESHNESS_KEYWORDS = [
    "deadline", "when", "date", "current", "this semester", "upcoming",
    "next", "today", "tomorrow", "registration date",
]


@dataclass
class RouterResult:
    intent: str  # registrar_calendar | catalog_course | rmp_user_provided | general | unknown
    freshness_strategy: str  # indexed | live_fetch | hybrid
    source_filter: str | None = None  # source name filter for retrieval


def classify_query(query: str, has_rmp_excerpt: bool = False) -> RouterResult:
    """Classify query intent and decide freshness strategy using rules."""
    q = query.lower().strip()

    # RMP user-provided mode
    if has_rmp_excerpt or any(kw in q for kw in RMP_KEYWORDS):
        return RouterResult(
            intent="rmp_user_provided",
            freshness_strategy="indexed",
            source_filter=None,
        )

    # Calendar / registrar
    cal_score = sum(1 for kw in CALENDAR_KEYWORDS if kw in q)
    cat_score = sum(1 for kw in CATALOG_KEYWORDS if kw in q)

    if cal_score > cat_score and cal_score > 0:
        # Check freshness need
        needs_fresh = any(kw in q for kw in FRESHNESS_KEYWORDS)
        return RouterResult(
            intent="registrar_calendar",
            freshness_strategy="live_fetch" if needs_fresh else "indexed",
            source_filter="gt-registrar",
        )

    if cat_score > 0:
        return RouterResult(
            intent="catalog_course",
            freshness_strategy="indexed",
            source_filter="gt-catalog",
        )

    # General / unknown
    needs_fresh = any(kw in q for kw in FRESHNESS_KEYWORDS)
    return RouterResult(
        intent="general",
        freshness_strategy="hybrid" if needs_fresh else "indexed",
    )
