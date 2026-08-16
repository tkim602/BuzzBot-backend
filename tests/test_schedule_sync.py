from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from pathlib import Path

import httpx
import pytest

from ingestion.probes.core import ProbeStatus
from ingestion.schedule import cli
from ingestion.schedule.sync import SyncOutcome, SyncResult, build_subject_listing_url, sync_subject

FIXTURE = Path("tests/fixtures/oscar_schedule_sample.html")


def _never_open_database():
    raise AssertionError("database must not be opened")


def test_build_subject_listing_url_is_the_exact_public_oscar_query():
    assert build_subject_listing_url("202608", "cs") == (
        "https://oscar.gatech.edu/bprod/bwckctlg.p_disp_listcrse"
        "?term_in=202608&subj_in=CS&crse_in=&schd_in=%25"
    )


@pytest.mark.asyncio
async def test_probe_failure_prevents_subject_fetch_and_database_access(tmp_path: Path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="<html>changed</html>", request=request)

    result = await sync_subject(
        "202608",
        "CS",
        "7650",
        tmp_path,
        _never_open_database,
        httpx.MockTransport(handler),
    )

    assert result.outcome is SyncOutcome.PROBE_FAILED
    assert result.probe_status is ProbeStatus.PARSE_FAILED
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_sync_probe_stops_before_auth_redirect(tmp_path: Path):
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://sso.gatech.edu/login"},
        )

    result = await sync_subject(
        "202608",
        "CS",
        "7650",
        tmp_path,
        _never_open_database,
        httpx.MockTransport(handler),
    )

    assert result.outcome is SyncOutcome.PROBE_FAILED
    assert result.probe_status is ProbeStatus.AUTH_REQUIRED
    assert result.requests_used == len(urls) == 1
    assert httpx.URL(urls[0]).host == "oscar.gatech.edu"


@pytest.mark.asyncio
async def test_ready_probe_allows_exactly_one_subject_request_and_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    html = FIXTURE.read_text()
    urls: list[str] = []
    published_version = uuid.UUID("b561fa08-9975-4fbd-a8f4-a50b11163314")

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(
            200,
            text=html,
            request=request,
            headers={"Content-Type": "text/html", "Set-Cookie": "secret=value"},
        )

    monkeypatch.setattr(
        "ingestion.schedule.sync.publish_collection",
        lambda *args, **kwargs: published_version,
    )

    result = await sync_subject(
        "202608",
        "cs",
        "7650",
        tmp_path,
        lambda: nullcontext(object()),
        httpx.MockTransport(handler),
    )

    assert urls == [
        "https://oscar.gatech.edu/bprod/bwckctlg.p_disp_listcrse"
        "?term_in=202608&subj_in=CS&crse_in=7650&schd_in=%25",
        build_subject_listing_url("202608", "CS"),
    ]
    assert result == SyncResult(
        outcome=SyncOutcome.PUBLISHED,
        probe_status=ProbeStatus.READY,
        requests_used=2,
        records_fetched=2,
        records_parsed=2,
        failures=0,
        courses=1,
        sections=2,
        meetings=2,
        version_id=published_version,
        reason=None,
    )
    snapshots = list(tmp_path.glob("subject-*.html"))
    assert len(snapshots) == 1
    assert snapshots[0].read_text() == html
    assert "secret=value" not in "\n".join(path.read_text() for path in tmp_path.iterdir())


@pytest.mark.asyncio
async def test_subject_429_stops_without_database_publication(tmp_path: Path):
    html = FIXTURE.read_text()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, text=html, request=request)
        return httpx.Response(429, request=request, headers={"Retry-After": "60"})

    result = await sync_subject(
        "202608",
        "CS",
        "7650",
        tmp_path,
        _never_open_database,
        httpx.MockTransport(handler),
    )

    assert result.outcome is SyncOutcome.RATE_LIMITED
    assert result.requests_used == 2
    assert result.reason == "HTTP_429"
    assert calls == 2


@pytest.mark.asyncio
async def test_subject_transient_failure_is_not_retried(tmp_path: Path):
    html = FIXTURE.read_text()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, text=html, request=request)
        return httpx.Response(503, request=request)

    result = await sync_subject(
        "202608",
        "CS",
        "7650",
        tmp_path,
        _never_open_database,
        httpx.MockTransport(handler),
    )

    assert result.outcome is SyncOutcome.FETCH_FAILED
    assert result.requests_used == 2
    assert calls == 2


@pytest.mark.asyncio
async def test_subject_auth_redirect_is_not_followed_or_published(tmp_path: Path):
    html = FIXTURE.read_text()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, text=html, request=request)
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://sso.gatech.edu/login"},
        )

    result = await sync_subject(
        "202608",
        "CS",
        "7650",
        tmp_path,
        _never_open_database,
        httpx.MockTransport(handler),
    )

    assert result.outcome is SyncOutcome.AUTH_REQUIRED
    assert calls == 2


@pytest.mark.asyncio
async def test_unparseable_subject_body_stops_without_database_access(tmp_path: Path):
    probe_html = FIXTURE.read_text()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=probe_html if calls == 1 else "", request=request)

    result = await sync_subject(
        "202608",
        "CS",
        "7650",
        tmp_path,
        _never_open_database,
        httpx.MockTransport(handler),
    )

    assert result.outcome is SyncOutcome.PARSE_FAILED
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("html", "expected_code"),
    [
        (
            FIXTURE.read_text().replace(
                "Natural Language - 90427 - CS 7650 - A",
                "invalid section header",
            ),
            "SECTION_HEADER_INVALID",
        ),
        (
            FIXTURE.read_text().replace("3:30 pm - 4:45 pm", "not a time"),
            "MEETING_INVALID",
        ),
    ],
)
async def test_record_failures_reconcile_fetched_parsed_and_failure_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    html: str,
    expected_code: str,
):
    good_probe = FIXTURE.read_text()
    calls = 0
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = good_probe if calls == 1 else html
        return httpx.Response(200, text=body, request=request)

    def capture_publish(*args, **kwargs):
        captured["plan"] = args[4]
        captured["failures"] = args[7]
        captured["report"] = args[8]
        return uuid.UUID("8b21cc07-4449-47bb-9346-4643d723d4d3")

    monkeypatch.setattr("ingestion.schedule.sync.publish_collection", capture_publish)

    result = await sync_subject(
        "202608",
        "CS",
        "7650",
        tmp_path,
        lambda: nullcontext(object()),
        httpx.MockTransport(handler),
    )

    plan = captured["plan"]
    failures = captured["failures"]
    report = captured["report"]
    assert result.outcome is SyncOutcome.VALIDATION_FAILED
    assert plan.records_fetched == plan.records_parsed + len(failures)
    assert result.records_fetched == result.records_parsed + result.failures
    assert expected_code in {failure.error_code for failure in failures}
    assert report.valid is False


def test_cli_prints_one_compact_count_only_json_line(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    async def fake_sync(*args, **kwargs):
        return SyncResult(
            SyncOutcome.PUBLISHED,
            ProbeStatus.READY,
            2,
            2,
            2,
            0,
            1,
            2,
            2,
            uuid.UUID("b561fa08-9975-4fbd-a8f4-a50b11163314"),
            None,
        )

    monkeypatch.setattr(cli, "sync_subject", fake_sync)

    exit_code = cli.main(["--term", "202608", "--subject", "CS", "--probe-course", "7650"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert len(output.splitlines()) == 1
    assert json.loads(output) == {
        "outcome": "PUBLISHED",
        "probe_status": "READY",
        "requests_used": 2,
        "records_fetched": 2,
        "records_parsed": 2,
        "failures": 0,
        "courses": 1,
        "sections": 2,
        "meetings": 2,
        "version_id": "b561fa08-9975-4fbd-a8f4-a50b11163314",
        "reason": None,
    }
    assert "Natural Language" not in output
