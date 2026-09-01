from __future__ import annotations

from typing import cast

from app.graph.state import CitationItem, EvidenceItem, ScheduleQueryType

_TERMS = {"02": "Spring", "05": "Summer", "08": "Fall"}


def _term(code: str) -> str:
    return f"{_TERMS.get(code[-2:], code[-2:])} {code[:4]}"


def _campus(value: str) -> str:
    if value == "Georgia Tech-Atlanta * Campus":
        return "Atlanta"
    if value in {"Online Campus", "Video Campus"}:
        return value.removesuffix(" Campus")
    if value.startswith("GT Lorraine-"):
        return "GT Lorraine"
    return value.removesuffix(" Campus")


def _time(value: str) -> str:
    hour, minute = (int(part) for part in value.split(":")[:2])
    suffix = "AM" if hour < 12 else "PM"
    hour = hour % 12 or 12
    return f"{hour}:{minute:02d} {suffix}"


def _meeting(value: dict[str, object], *, location_only: bool = False) -> str:
    location = " ".join(str(part) for part in (value.get("building"), value.get("room")) if part)
    if location_only:
        return location or "location TBA"
    if value.get("is_tba") or not value.get("start_time") or not value.get("end_time"):
        return "meeting time TBA"
    when = (
        f"{value.get('days') or 'days TBA'}, "
        f"{_time(str(value['start_time']))}–{_time(str(value['end_time']))}"
    )
    return f"{when} — {location}" if location else when


def _metadata(evidence: list[EvidenceItem]) -> list[dict[str, object]]:
    return sorted(
        (item["metadata"] for item in evidence),
        key=lambda row: (str(row["section_code"]), str(row["crn"])),
    )


def _citations(evidence: list[EvidenceItem]) -> list[CitationItem]:
    return [
        CitationItem(
            url=item["url"],
            title=item["title"],
            fetched_at=item["fetched_at"],
            quote=item["text"],
        )
        for item in evidence
    ]


def render_schedule_answer(
    query_type: ScheduleQueryType, evidence: list[EvidenceItem]
) -> tuple[str, list[CitationItem]]:
    rows = _metadata(evidence)
    if not rows:
        raise ValueError("schedule evidence is required")
    first = rows[0]
    code = f"{first['subject']} {first['course_number']}"
    term = _term(str(first["term_code"]))
    title = str(first["title"])

    if query_type == "offering":
        sections = ", ".join(
            f"{row['section_code']} ({_campus(str(row['campus']))})" for row in rows
        )
        answer = (
            f"Yes. {code} ({title}) is offered in {term} with {len(rows)} "
            f"{'section' if len(rows) == 1 else 'sections'}: {sections}."
        )
    elif query_type in {"sections", "crns"}:
        bullets = "\n".join(
            f"- {row['section_code']} — CRN {row['crn']} — {_campus(str(row['campus']))}"
            for row in rows
        )
        answer = f"{code} has {len(rows)} sections in {term}:\n\n{bullets}"
    elif query_type == "instructors":
        bullets = "\n".join(
            f"- {row['section_code']} — "
            f"{', '.join(cast(list[str], row['instructors'])) or 'Instructor TBA'}"
            for row in rows
        )
        answer = f"{term} {code} instructors:\n\n{bullets}"
    elif query_type in {"meeting", "location"}:
        lines = []
        for row in rows:
            meetings = cast(list[dict[str, object]], row["meetings"])
            details = "; ".join(
                _meeting(meeting, location_only=query_type == "location") for meeting in meetings
            ) or ("location TBA" if query_type == "location" else "meeting time TBA")
            lines.append(f"- {row['section_code']} — {details}")
        label = "locations" if query_type == "location" else "meeting information"
        answer = f"{term} {code} {label}:\n\n" + "\n".join(lines)
    elif query_type == "online_availability":
        online = [
            str(row["section_code"])
            for row in rows
            if "online" in f"{row['campus']} {row.get('instructional_method') or ''}".lower()
        ]
        if online:
            answer = f"Yes. {code} has an online section listed in {term}: {', '.join(online)}."
        else:
            answer = f"No. The stored {term} schedule does not list an online section for {code}."
    else:
        lines = []
        for row in rows:
            instructors = ", ".join(cast(list[str], row["instructors"])) or "Instructor TBA"
            meetings = cast(list[dict[str, object]], row["meetings"])
            meeting = "; ".join(_meeting(value) for value in meetings) or "meeting time TBA"
            lines.append(
                f"- {row['section_code']} — CRN {row['crn']} — "
                f"{_campus(str(row['campus']))} — {instructors} — {meeting}"
            )
        answer = f"{term} {code} ({title}) schedule:\n\n" + "\n".join(lines)
    return answer, _citations(evidence)


def validate_schedule_answer(
    query_type: ScheduleQueryType,
    evidence: list[EvidenceItem],
    answer: str,
    citations: list[CitationItem],
) -> bool:
    expected_answer, expected_citations = render_schedule_answer(query_type, evidence)
    return answer == expected_answer and citations == expected_citations
