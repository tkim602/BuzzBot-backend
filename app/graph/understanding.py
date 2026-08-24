from __future__ import annotations

import re

from app.graph.state import GraphIntent, ScheduleQueryType
from app.rag.router import classify_query, extract_course_code
from app.retrieval.documents import policy_source_types

TERM_RE = re.compile(r"\b(spring|summer|fall)\s*(20\d{2})\b", re.IGNORECASE)
REVERSED_TERM_RE = re.compile(r"\b(20\d{2})\s*(spring|summer|fall)\b", re.IGNORECASE)
TERM_SUFFIX = {"spring": "02", "summer": "05", "fall": "08"}


def _schedule_query_type(text: str) -> ScheduleQueryType:
    lowered = text.lower()
    if re.search(r"\bonline\b|\bremote\b", lowered):
        return "online_availability"
    if re.search(r"\bcrns?\b", lowered):
        return "crns"
    if re.search(
        r"\bwho(?:'s|s)?\b|\binstructors?\b|\bprofessors?\b|"
        r"\bteach(?:es|ing|ers?)?\b|\btaught\b",
        lowered,
    ):
        return "instructors"
    if re.search(r"\bwhere\b|\blocations?\b|\bbuildings?\b|\brooms?\b", lowered):
        return "location"
    if re.search(r"\bwhen\b|\btimes?\b|\bmeets?\b|\bdays?\b", lowered):
        return "meeting"
    if re.search(r"\bsections?\b", lowered):
        return "sections"
    if re.search(r"\boffer(?:ed|ing|s)?\b|\brun(?:s|ning)?\b", lowered):
        return "offering"
    return "general_schedule"


def _schedule_follow_up(text: str) -> bool:
    return bool(
        re.search(r"\bit\b|\bthat (?:class|course)\b|\bwhich one\b", text, re.I)
        and re.search(
            r"\bsections?\b|\bcrns?\b|\bteaches?\b|\binstructors?\b|\bprofessors?\b|"
            r"\bmeets?\b|\btimes?\b|\bwhere\b|\blocations?\b|\bonline\b",
            text,
            re.I,
        )
    )


def _course(query: str) -> tuple[str, str] | None:
    course_code = extract_course_code(query)
    if course_code is None:
        return None
    subject, number = course_code.split()
    return subject, number


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


def _explicit_calendar_event(text: str, term_code: str | None) -> bool:
    lowered = text.lower()
    if not term_code or any(
        cue in lowered
        for cue in ("admission", "application", "financial aid", "tuition", "fee payment")
    ):
        return False
    temporal = re.search(r"\bwhen\b|\bdeadline\b|\bdue\b|\bavailable online\b", lowered)
    events = (
        "classes",
        "payment",
        "final grade",
        "thesis",
        "final exam",
        "commencement",
        "break",
        "holiday",
        "registration",
        "withdraw",
        "grade mode",
        "reading period",
        "end of term",
    )
    return bool(temporal and any(event in lowered for event in events))


def understand_query(
    query: str,
    user_term: str | None = None,
    context: dict[str, object] | None = None,
    active_term: str | None = None,
) -> dict[str, object]:
    text = query.strip()
    if not text:
        raise ValueError("query is required")

    route = classify_query(text)
    domain_policy = policy_source_types(text)
    course = _course(text)
    term_code = _term_code(text) or (_term_code(user_term) if user_term else None)
    previous_schedule = bool(context and context.get("intent") == "course_schedule")
    schedule_follow_up = _schedule_follow_up(text)
    what_about = text.lower().startswith("what about ") and previous_schedule
    number_tokens = re.findall(r"\b\d{4}[a-z]?\b", text, re.I)
    incomplete_schedule = bool(
        course is None
        and any(token != (term_code or "")[:4] for token in number_tokens)
        and _schedule_query_type(text) != "general_schedule"
    )
    if previous_schedule and (schedule_follow_up or what_about):
        if course is None and schedule_follow_up:
            previous_subject = context.get("subject")
            previous_number = context.get("course_number")
            if isinstance(previous_subject, str) and isinstance(previous_number, str):
                course = previous_subject, previous_number
        if term_code is None and isinstance(context.get("term_code"), str):
            term_code = str(context["term_code"])

    intent: GraphIntent
    if (
        route.intent == "course_schedule_sections"
        or schedule_follow_up
        or what_about
        or incomplete_schedule
    ):
        intent = "course_schedule"
    elif _explicit_calendar_event(text, term_code) or (
        route.intent == "registrar_calendar" and not domain_policy
    ):
        intent = "registration_calendar"
    elif route.intent == "catalog_course" and course is not None:
        intent = "course_details"
    else:
        intent = "policy"

    if intent == "course_schedule" and term_code is None and active_term:
        term_code = active_term

    result: dict[str, object] = {
        "intent": intent,
        "subject": None,
        "course_number": None,
        "term_code": None,
        "needs_clarification": False,
        "retry_count": 0,
        "evidence": [],
        "citations": [],
        "notes": [],
        "answer": "",
        "answer_valid": False,
    }
    if course is not None:
        result["subject"], result["course_number"] = course
    if term_code is not None:
        result["term_code"] = term_code
    if intent == "course_schedule":
        result["schedule_query_type"] = _schedule_query_type(text)

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
