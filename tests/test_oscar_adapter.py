from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ingestion.probes.core import ProbeStatus
from ingestion.schedule.oscar import (
    build_subject_discovery_url,
    discover_subjects,
    parse_subjects,
)

FIXTURE = Path("tests/fixtures/oscar_subjects_sample.html")


def test_subject_discovery_url_is_the_public_banner_term_form():
    assert build_subject_discovery_url("202608") == (
        "https://oscar.gatech.edu/bprod/bwckgens.p_proc_term_date"
        "?p_calling_proc=bwckschd.p_disp_dyn_sched&p_term=202608"
    )
    with pytest.raises(ValueError, match="six digits"):
        build_subject_discovery_url("fall-2026")


def test_subject_parser_preserves_source_order_and_deduplicates():
    assert parse_subjects(FIXTURE.read_text()) == ("AE", "CS", "ECE")


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>no subject select</body></html>",
        "<select name='sel_subj'><option value='not valid'>bad</option></select>",
    ],
)
def test_subject_parser_rejects_incompatible_markup(html: str):
    with pytest.raises(ValueError, match="subject"):
        parse_subjects(html)


@pytest.mark.asyncio
async def test_discovery_makes_one_request_and_returns_subjects():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text=FIXTURE.read_text(), request=request)

    result = await discover_subjects("202608", httpx.MockTransport(handler))

    assert result.status is ProbeStatus.READY
    assert result.subjects == ("AE", "CS", "ECE")
    assert result.requests_used == len(requests) == 1


@pytest.mark.asyncio
async def test_discovery_classifies_rate_limit_without_retry():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, request=request, headers={"Retry-After": "45"})

    result = await discover_subjects("202608", httpx.MockTransport(handler))

    assert result.status is ProbeStatus.RATE_LIMITED
    assert result.retry_after_seconds == 45
    assert result.requests_used == calls == 1


@pytest.mark.asyncio
async def test_discovery_stops_before_auth_redirect():
    hosts: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://sso.gatech.edu/login"},
        )

    result = await discover_subjects("202608", httpx.MockTransport(handler))

    assert result.status is ProbeStatus.AUTH_REQUIRED
    assert hosts == ["oscar.gatech.edu"]
