from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

from lxml import html as lxml_html

from ingestion.probes.core import (
    ProbeHttpResponse,
    ProbeResult,
    ProbeSession,
    ProbeStatus,
)

OSCAR_LISTING_URL = "https://oscar.gatech.edu/bprod/bwckctlg.p_disp_listcrse"
SECTION_TITLE_RE = re.compile(
    r"^(?P<title>.+?)\s+-\s+(?P<crn>\d+)\s+-\s+"
    r"(?P<subject>[A-Z]+)\s+(?P<course>[A-Z0-9]+)\s+-\s+(?P<section>\S+)$"
)
CREDITS_RE = re.compile(r"(\d+(?:\.\d+)?)\s+Credits", re.IGNORECASE)
AUTH_HOSTS = {"sso.gatech.edu", "login.gatech.edu", "authn.gatech.edu"}


@dataclass(frozen=True)
class OscarMeetingSample:
    meeting_type: str
    time: str
    days: str
    location: str
    date_range: str
    schedule_type: str
    instructor: str


@dataclass(frozen=True)
class OscarSectionSample:
    title: str
    crn: str
    subject: str
    course: str
    section: str
    term_name: str
    campus: str
    credits: float
    meetings: tuple[OscarMeetingSample, ...]
    schedule_type: str = ""


@dataclass(frozen=True)
class OscarParseFailure:
    error_code: str
    raw_header: str


def build_listing_url(term: str, subject: str, course: str) -> str:
    query = urlencode(
        {
            "term_in": term,
            "subj_in": subject.upper(),
            "crse_in": course.upper(),
            "schd_in": "%",
        }
    )
    return f"{OSCAR_LISTING_URL}?{query}"


def parse_schedule_listing(
    html: str,
    max_records: int | None,
) -> tuple[list[OscarSectionSample], list[OscarParseFailure]]:
    tree = lxml_html.fromstring(html)
    tables = tree.xpath("//table[caption[normalize-space(.)='Sections Found']]")
    if not tables:
        return [], []

    sections: list[OscarSectionSample] = []
    failures: list[OscarParseFailure] = []
    records_seen = 0
    rows = tables[0].xpath("./tr | ./tbody/tr")
    for row in rows:
        title_cells = row.xpath(
            "./th[contains(concat(' ', normalize-space(@class), ' '), ' ddtitle ')]"
        )
        if not title_cells:
            continue
        if max_records is not None and records_seen >= max_records:
            break
        records_seen += 1
        raw_header = _text(title_cells[0])
        match = SECTION_TITLE_RE.match(raw_header)
        if not match:
            failures.append(OscarParseFailure("SECTION_HEADER_INVALID", raw_header))
            continue
        detail_rows = row.xpath("following-sibling::tr[1]")
        if not detail_rows:
            failures.append(OscarParseFailure("SECTION_DETAIL_MISSING", raw_header))
            continue
        detail_cells = detail_rows[0].xpath("./td")
        if not detail_cells:
            failures.append(OscarParseFailure("SECTION_DETAIL_MISSING", raw_header))
            continue

        detail = detail_cells[0]
        detail_text = _text(detail)
        credits_match = CREDITS_RE.search(detail_text)
        term_name = _field_tail(detail, "Associated Term:")
        campus = next(
            (line for line in _lines(detail) if line.endswith("Campus")),
            "",
        )
        schedule_type = next(
            (
                line.removesuffix("Schedule Type").strip().removesuffix("*").strip()
                for line in _lines(detail)
                if line.endswith("Schedule Type")
            ),
            "",
        )
        meetings = _parse_meetings(detail)
        sections.append(
            OscarSectionSample(
                title=match.group("title"),
                crn=match.group("crn"),
                subject=match.group("subject"),
                course=match.group("course"),
                section=match.group("section"),
                term_name=term_name,
                campus=campus,
                credits=float(credits_match.group(1)) if credits_match else 0.0,
                meetings=tuple(meetings),
                schedule_type=schedule_type,
            )
        )
    return sections, failures


async def probe_oscar(
    session: ProbeSession,
    term: str,
    subject: str,
    course: str,
) -> tuple[ProbeResult, ProbeHttpResponse | None]:
    response = await session.get(build_listing_url(term, subject, course))

    if requires_auth(response):
        return _result(
            session, ProbeStatus.AUTH_REQUIRED, response, reason="LOGIN_REDIRECT"
        ), response
    if response.status_code == 429:
        retry_header = response.retry_after
        retry_after = int(retry_header) if retry_header and retry_header.isdigit() else None
        return (
            _result(
                session,
                ProbeStatus.RATE_LIMITED,
                response,
                reason="HTTP_429",
                retry_after_seconds=retry_after,
            ),
            response,
        )
    if response.status_code in {401, 403}:
        return _result(session, ProbeStatus.AUTH_REQUIRED, response, reason="HTTP_AUTH"), response
    if response.status_code != 200:
        reason = response.error or f"HTTP_{response.status_code}"
        return _result(session, ProbeStatus.UNAVAILABLE, response, reason=reason), response

    try:
        sections, _ = parse_schedule_listing(response.body, session.budget.max_records)
    except (ValueError, TypeError) as exc:
        return _result(session, ProbeStatus.PARSE_FAILED, response, reason=str(exc)), response
    if not sections:
        return _result(session, ProbeStatus.PARSE_FAILED, response, reason="NO_SECTIONS"), response

    required_fields = all(
        section.crn
        and section.subject
        and section.course
        and section.section
        and section.term_name
        and section.campus
        and section.meetings
        for section in sections
    )
    status = ProbeStatus.READY if required_fields else ProbeStatus.PARSE_FAILED
    return (
        _result(
            session,
            status,
            response,
            parsed_records=len(sections),
            required_fields_present=required_fields,
            reason=None if required_fields else "REQUIRED_FIELDS_MISSING",
        ),
        response,
    )


def _parse_meetings(detail) -> list[OscarMeetingSample]:
    meetings: list[OscarMeetingSample] = []
    tables = detail.xpath(
        ".//table[caption[contains(normalize-space(.), 'Scheduled Meeting Times')]]"
    )
    for table in tables:
        rows = table.xpath("./tr | ./tbody/tr")
        if not rows:
            continue
        headers = [_text(cell) for cell in rows[0].xpath("./th")]
        for row in rows[1:]:
            values = [_text(cell) for cell in row.xpath("./td")]
            if not values:
                continue
            data = dict(zip(headers, values, strict=False))
            instructor = re.sub(r"\s*\(\s*P\s*\)\s*$", "", data.get("Instructors", ""))
            meetings.append(
                OscarMeetingSample(
                    meeting_type=data.get("Type", ""),
                    time=data.get("Time", ""),
                    days=data.get("Days", ""),
                    location=data.get("Where", ""),
                    date_range=data.get("Date Range", ""),
                    schedule_type=data.get("Schedule Type", ""),
                    instructor=instructor,
                )
            )
    return meetings


def _result(
    session: ProbeSession,
    status: ProbeStatus,
    response: ProbeHttpResponse,
    *,
    parsed_records: int = 0,
    required_fields_present: bool = False,
    reason: str | None = None,
    retry_after_seconds: int | None = None,
) -> ProbeResult:
    return ProbeResult(
        provider="public-oscar",
        status=status,
        reachable=response.status_code > 0,
        public_access=status in {ProbeStatus.READY, ProbeStatus.PARSE_FAILED},
        parsed_records=parsed_records,
        required_fields_present=required_fields_present,
        requests_used=session.requests_used,
        latency_ms=session.latency_ms,
        reason=reason,
        retry_after_seconds=retry_after_seconds,
    )


def requires_auth(response: ProbeHttpResponse) -> bool:
    urls = (*response.redirect_urls, response.final_url)
    for url in urls:
        parsed = urlparse(url)
        if parsed.hostname in AUTH_HOSTS or "/login" in parsed.path.lower():
            return True
    return False


def _field_tail(element, label: str) -> str:
    matches = element.xpath(f".//span[normalize-space(.)='{label}']")
    return _clean(matches[0].tail or "") if matches else ""


def _lines(element) -> list[str]:
    return [_clean(line) for line in element.text_content().splitlines() if _clean(line)]


def _text(element) -> str:
    return _clean(element.text_content())


def _clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())
