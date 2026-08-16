from pathlib import Path

import httpx
import pytest

from ingestion.probes.core import ProbeBudget, ProbeSession, ProbeStatus
from ingestion.probes.oscar import build_listing_url, parse_schedule_listing, probe_oscar

FIXTURE = Path("tests/fixtures/oscar_schedule_sample.html")


def test_parse_schedule_listing_extracts_structured_fields():
    sections, failures = parse_schedule_listing(FIXTURE.read_text(), max_records=20)

    assert len(sections) == 2
    assert sections[0].crn == "90427"
    assert sections[0].subject == "CS"
    assert sections[0].course == "7650"
    assert sections[0].section == "A"
    assert sections[0].term_name == "Fall 2026"
    assert sections[0].campus == "Georgia Tech-Atlanta * Campus"
    assert sections[0].credits == 3.0
    assert sections[0].meetings[0].time == "3:30 pm - 4:45 pm"
    assert sections[0].meetings[0].days == "MW"
    assert sections[0].meetings[0].location == "Paper Tricentennial 109"
    assert sections[0].meetings[0].instructor == "Kartik Goyal"
    assert sections[1].meetings[0].time == "TBA"
    assert sections[1].meetings[0].days == ""
    assert failures == []


def test_parse_schedule_listing_stops_at_record_limit():
    sections, failures = parse_schedule_listing(FIXTURE.read_text(), max_records=1)

    assert [section.crn for section in sections] == ["90427"]
    assert failures == []


def test_parse_schedule_listing_unlimited_retains_malformed_sections():
    malformed = FIXTURE.read_text().replace(
        "Natural Language - 90427 - CS 7650 - A",
        "invalid section header",
    )

    sections, failures = parse_schedule_listing(malformed, max_records=None)

    assert [section.crn for section in sections] == ["89627"]
    assert len(failures) == 1
    assert failures[0].error_code == "SECTION_HEADER_INVALID"


def test_build_listing_url_encodes_query():
    url = build_listing_url("202608", "CS", "7650")

    assert url == (
        "https://oscar.gatech.edu/bprod/bwckctlg.p_disp_listcrse"
        "?term_in=202608&subj_in=CS&crse_in=7650&schd_in=%25"
    )


@pytest.mark.asyncio
async def test_probe_classifies_public_listing_as_ready():
    html = FIXTURE.read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=html,
            request=request,
            headers={"Content-Type": "text/html"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        session = ProbeSession(client, ProbeBudget())
        result, _ = await probe_oscar(session, "202608", "CS", "7650")

    assert result.status is ProbeStatus.READY
    assert result.public_access is True
    assert result.parsed_records == 2
    assert result.required_fields_present is True


@pytest.mark.asyncio
async def test_probe_classifies_429_without_retry():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, request=request, headers={"Retry-After": "60"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        session = ProbeSession(client, ProbeBudget())
        result, _ = await probe_oscar(session, "202608", "CS", "7650")

    assert calls == 1
    assert result.status is ProbeStatus.RATE_LIMITED
    assert result.retry_after_seconds == 60


@pytest.mark.asyncio
async def test_probe_rejects_login_redirect():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"Location": "https://sso.gatech.edu/login"},
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        session = ProbeSession(client, ProbeBudget())
        result, _ = await probe_oscar(session, "202608", "CS", "7650")

    assert result.status is ProbeStatus.AUTH_REQUIRED
    assert result.public_access is False
    assert result.requests_used == calls == 1


@pytest.mark.asyncio
async def test_probe_rejects_incompatible_html():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>changed</html>", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        session = ProbeSession(client, ProbeBudget())
        result, _ = await probe_oscar(session, "202608", "CS", "7650")

    assert result.status is ProbeStatus.PARSE_FAILED
    assert result.parsed_records == 0
