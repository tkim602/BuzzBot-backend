"""Query router — classify intent and decide freshness strategy."""

from __future__ import annotations

import re
from dataclasses import dataclass

CALENDAR_KEYWORDS = [
    "deadline",
    "registration",
    "register",
    "calendar",
    "drop",
    "add",
    "withdrawal",
    "final exam",
    "commencement",
    "holiday",
    "break",
    "midterm",
    "academic year",
    "tentative",
    "exam matrix",
    "학사일정",
    "등록",
    "수강신청",
    "마감",
    "기말고사",
    "졸업식",
]
CATALOG_KEYWORDS = [
    "course",
    "class",
    "credit",
    "prerequisite",
    "degree",
    "major",
    "minor",
    "program",
    "curriculum",
    "syllabus",
    "description",
    "catalog",
    "department",
    "school",
]
FRESHNESS_KEYWORDS = [
    "deadline",
    "when",
    "date",
    "current",
    "this semester",
    "upcoming",
    "next",
    "today",
    "tomorrow",
    "registration date",
    "언제",
    "오늘",
    "내일",
    "현재",
    "최신",
]
SCHEDULE_KEYWORDS = [
    "offer",
    "offered",
    "offering",
    "available",
    "availability",
    "section",
    "sections",
    "crn",
    "seat",
    "seats",
    "waitlist",
    "instructor",
    "professor",
    "time",
    "times",
    "location",
    "schedule",
    "개설",
    "열리",
    "강의",
    "수업",
    "시간표",
    "좌석",
    "담당교수",
]
TERM_KEYWORDS = [
    "spring",
    "summer",
    "fall",
    "semester",
    "term",
    "봄",
    "여름",
    "가을",
    "학기",
]
COURSE_CODE_RE = re.compile(r"\b([a-z]{2,4})\s*-?\s*(\d{4}[a-z]?)\b", re.IGNORECASE)
COURSE_CODE_STOPWORDS = {
    "spring",
    "summer",
    "fall",
    "term",
    "year",
    "this",
    "next",
    "last",
    "the",
}
LIBRARY_KEYWORDS = [
    "library",
    "libraries",
    "interlibrary",
    "study room",
    "borrow",
    "loan",
    "도서관",
    "대출",
    "스터디룸",
]
ADMISSION_DEADLINE_KEYWORDS = [
    "application deadline",
    "admission deadline",
    "deadline to apply",
]
ADMISSION_CONTEXT_KEYWORDS = [
    "omscs",
    "mscs",
    "graduate admission",
    "grad admission",
    "admission",
    "apply",
    "application",
]
REGISTRAR_PRIORITY_KEYWORDS = [
    "register",
    "registration",
    "add/drop",
    "withdraw",
    "withdrawal",
    "academic calendar",
]
FIRST_YEAR_POLICY_KEYWORDS = [
    "first-year",
    "first year",
    "early action",
    "common app",
    "intended major",
    "major selection",
]


def extract_course_code(query: str) -> str | None:
    for match in COURSE_CODE_RE.finditer(query):
        if match.group(1).lower() not in COURSE_CODE_STOPWORDS:
            return f"{match.group(1).upper()} {match.group(2).upper()}"
    return None


@dataclass
class RouterResult:
    intent: str
    freshness_strategy: str  # indexed | live_fetch | hybrid
    source_filter: str | list[str] | None = None  # source name filter for retrieval


def classify_query(query: str) -> RouterResult:
    """Classify query intent and decide freshness strategy using rules."""
    q = query.lower().strip()

    # Explicit course schedule intent (prefer gt-scheduler over registrar pages)
    has_course_code = bool(extract_course_code(q))
    has_schedule_keyword = any(kw in q for kw in SCHEDULE_KEYWORDS)
    has_term_keyword = any(kw in q for kw in TERM_KEYWORDS)
    has_calendar_keyword = any(kw in q for kw in CALENDAR_KEYWORDS)
    has_admission_deadline = any(kw in q for kw in ADMISSION_DEADLINE_KEYWORDS)
    has_admission_context = any(kw in q for kw in ADMISSION_CONTEXT_KEYWORDS)
    has_registrar_priority = (
        any(kw in q for kw in REGISTRAR_PRIORITY_KEYWORDS)
        or ("add" in q and "course" in q)
        or ("drop" in q and "course" in q)
    )

    if has_admission_deadline or (has_admission_context and "deadline" in q):
        has_omscs_token = bool(re.search(r"\bomscs\b", q))
        has_mscs_token = (
            bool(re.search(r"\bmscs\b", q)) or "master of science in computer science" in q
        )
        if any(keyword in q for keyword in FIRST_YEAR_POLICY_KEYWORDS):
            admission_sources = "gt-admission"
        elif has_mscs_token and not has_omscs_token:
            admission_sources = ["gt-catalog", "gt-grad", "gt-admission"]
        elif has_omscs_token:
            admission_sources = ["gt-omscs", "gt-admission", "gt-grad", "gt-catalog"]
        else:
            admission_sources = ["gt-omscs", "gt-admission", "gt-grad", "gt-catalog"]
        return RouterResult(
            intent="admissions_deadline",
            freshness_strategy="indexed",
            source_filter=admission_sources,
        )

    if "omscs" in q:
        return RouterResult("policy", "indexed", "gt-omscs")

    has_first_year_policy = any(keyword in q for keyword in FIRST_YEAR_POLICY_KEYWORDS)
    has_admission_recommendation = "recommendation" in q and has_admission_context
    if has_first_year_policy or has_admission_recommendation:
        return RouterResult("policy", "indexed", "gt-admission")

    if has_course_code and (has_schedule_keyword or has_term_keyword) and not has_calendar_keyword:
        return RouterResult(
            intent="course_schedule_sections",
            freshness_strategy="indexed",
            source_filter=["gt-scheduler", "gt-catalog"],
        )

    if any(kw in q for kw in LIBRARY_KEYWORDS):
        return RouterResult(
            intent="general",
            freshness_strategy="indexed",
            source_filter="gt-library",
        )

    # Calendar / registrar
    cal_score = sum(1 for kw in CALENDAR_KEYWORDS if kw in q)
    cat_score = sum(1 for kw in CATALOG_KEYWORDS if kw in q)

    if has_registrar_priority and cal_score > 0:
        needs_fresh = any(kw in q for kw in FRESHNESS_KEYWORDS)
        return RouterResult(
            intent="registrar_calendar",
            freshness_strategy="live_fetch" if needs_fresh else "indexed",
            source_filter=["gt-registrar", "gt-calendar-events"],
        )

    if cal_score > cat_score and cal_score > 0:
        # Check freshness need
        needs_fresh = any(kw in q for kw in FRESHNESS_KEYWORDS)
        return RouterResult(
            intent="registrar_calendar",
            freshness_strategy="live_fetch" if needs_fresh else "indexed",
            source_filter=["gt-registrar", "gt-calendar-events"],
        )

    if cat_score > 0:
        return RouterResult(
            intent="catalog_course",
            freshness_strategy="indexed",
            source_filter=["gt-catalog", "gt-scheduler"],
        )

    # Course code query without explicit schedule terms usually targets catalog facts.
    if has_course_code:
        return RouterResult(
            intent="catalog_course",
            freshness_strategy="indexed",
            source_filter=["gt-catalog", "gt-scheduler"],
        )

    # General / unknown
    needs_fresh = any(kw in q for kw in FRESHNESS_KEYWORDS)
    return RouterResult(
        intent="general",
        freshness_strategy="hybrid" if needs_fresh else "indexed",
    )
