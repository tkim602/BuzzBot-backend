from __future__ import annotations

import re

from app.graph.state import GraphIntent
from app.rag.router import classify_query

COURSE_RE = re.compile(r"\b([a-z]{2,4})\s*-?\s*(\d{4}[a-z]?)\b", re.IGNORECASE)
TERM_RE = re.compile(r"\b(spring|summer|fall)\s*(20\d{2})\b", re.IGNORECASE)
REVERSED_TERM_RE = re.compile(r"\b(20\d{2})\s*(spring|summer|fall)\b", re.IGNORECASE)
TERM_SUFFIX = {"spring": "02", "summer": "05", "fall": "08"}


def _course(query: str) -> tuple[str, str] | None:
    match = COURSE_RE.search(query)
    if match is None:
        return None
    return match.group(1).upper(), match.group(2).upper()


def _term_code(text: str) -> str | None:
    match = TERM_RE.search(text)
    if match is not None:
        term, year = match.group(1).lower(), match.group(2)
        return f"{year}{TERM_SUFFIX[term]}"
    match = REVERSED_TERM_RE.search(text)
    if match is not None:
        year, term = match.group(1), match.group(2).lower()
        return f"{year}{TERM_SUFFIX[term]}"
    return None


def understand_query(query: str, user_term: str | None = None) -> dict[str, object]:
    text = query.strip()
    if not text:
        raise ValueError("query is required")

    route = classify_query(text)
    course = _course(text)
    term_code = _term_code(text) or (_term_code(user_term) if user_term else None)

    intent: GraphIntent
    if route.intent == "course_schedule_sections":
        intent = "course_schedule"
    elif route.intent == "registrar_calendar":
        intent = "registration_calendar"
    elif route.intent == "catalog_course" and course is not None:
        intent = "course_details"
    else:
        intent = "policy"

    result: dict[str, object] = {
        "intent": intent,
        "needs_clarification": False,
        "retry_count": 0,
    }
    if course is not None:
        result["subject"], result["course_number"] = course
    if term_code is not None:
        result["term_code"] = term_code

    if intent == "course_schedule" and (course is None or term_code is None):
        missing = []
        if course is None:
            missing.append("course code")
        if term_code is None:
            missing.append("term")
        result.update(
            needs_clarification=True,
            clarification=(
                f"Please include the {' and '.join(missing)} (for example, CS 7650 in Fall 2026)."
            ),
        )
    return result
