from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit, urlunsplit

from lxml import html as lxml_html

MIN_CALENDAR_EVENTS = 25
SEMESTER_NAMES = {
    "2": "Spring",
    "5A": "Summer",
    "5E": "Summer-Early",
    "5F": "Summer-Full",
    "5L": "Summer-Late",
    "5M": "Summer-May",
    "8": "Fall",
}
_EDITION = re.compile(r"^20\d{2}-20\d{2}$")
_REQUIRED_FIELDS = ("id", "date", "semester", "year", "category", "event")
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)


class CalendarPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class CalendarDocument:
    edition: str
    title: str
    text: str
    event_count: int


def selected_academic_year(page_html: str) -> str | None:
    try:
        root = lxml_html.fromstring(page_html)
    except (TypeError, ValueError):
        return None
    values = root.xpath("//select[@id='academic-year']/option[@selected]/@value")
    if not values:
        return None
    edition = str(values[0]).strip()
    return edition if _EDITION.fullmatch(edition) else None


def calendar_request_url(seed_url: str, edition: str) -> str:
    parsed = urlsplit(seed_url)
    query = urlencode({"year": edition, "status": "current"})
    return urlunsplit((parsed.scheme, parsed.netloc, "/calevents/proxy", query, ""))


def calendar_request_headers(seed_url: str) -> dict[str, str]:
    return {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": seed_url,
        "User-Agent": _BROWSER_USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
    }


def parse_calendar_payload(edition: str, payload: object) -> CalendarDocument:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise CalendarPayloadError("INVALID_JSON_SHAPE")
    rows = payload["data"]
    if len(rows) < MIN_CALENDAR_EVENTS:
        raise CalendarPayloadError("TOO_FEW_EVENTS")

    blocks: list[tuple[int, str, str]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or any(
            not _has_text(row.get(field)) for field in _REQUIRED_FIELDS
        ):
            raise CalendarPayloadError("MISSING_REQUIRED_FIELD")

        event_text = _plain_text(str(row["event"]))
        if not event_text:
            raise CalendarPayloadError("EMPTY_EVENT_TEXT")
        try:
            weight = int(row.get("weight", index))
        except (TypeError, ValueError) as exc:
            raise CalendarPayloadError("INVALID_WEIGHT") from exc

        event_id = str(row["id"]).strip()
        semester_code = str(row["semester"]).strip()
        semester = SEMESTER_NAMES.get(semester_code, semester_code)
        year = str(row["year"]).strip()
        block = (
            f"## Georgia Tech Academic Calendar {edition} — Event {event_id}\n"
            f"Semester: {semester} {year}\n"
            f"Category: {str(row['category']).strip()}\n"
            f"Date: {str(row['date']).strip()}, {year}\n"
            f"Event: {event_text}"
        )
        blocks.append((weight, event_id, block))

    title = f"Georgia Tech Academic Calendar {edition}"
    text = "\n\n".join(block for _, _, block in sorted(blocks))
    return CalendarDocument(edition, title, text, len(rows))


def _has_text(value: object) -> bool:
    return value is not None and bool(str(value).strip())


def _plain_text(fragment: str) -> str:
    root = lxml_html.fragment_fromstring(fragment, create_parent="div")
    return " ".join(root.text_content().split())
