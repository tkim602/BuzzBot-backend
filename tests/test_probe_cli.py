from pathlib import Path

import httpx
import pytest

from ingestion.probes.cli import run_oscar_probe
from ingestion.probes.core import ProbeStatus


@pytest.mark.asyncio
async def test_run_oscar_probe_writes_safe_report_and_body(tmp_path: Path):
    html = Path("tests/fixtures/oscar_schedule_sample.html").read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=html,
            request=request,
            headers={"Content-Type": "text/html", "Set-Cookie": "session-secret"},
        )

    result = await run_oscar_probe(
        term="202608",
        subject="CS",
        course="7650",
        output_dir=tmp_path,
        transport=httpx.MockTransport(handler),
    )

    assert result.status is ProbeStatus.READY
    report = next(tmp_path.glob("*.json")).read_text()
    body = next(tmp_path.glob("*.html")).read_text()
    assert '"status": "READY"' in report
    assert '"requests_used": 1' in report
    assert "session-secret" not in report + body


@pytest.mark.asyncio
async def test_run_oscar_probe_stops_before_auth_redirect(tmp_path: Path):
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://sso.gatech.edu/login"},
        )

    result = await run_oscar_probe(
        term="202608",
        subject="CS",
        course="7650",
        output_dir=tmp_path,
        transport=httpx.MockTransport(handler),
    )

    assert result.status is ProbeStatus.AUTH_REQUIRED
    assert result.requests_used == len(urls) == 1
    assert httpx.URL(urls[0]).host == "oscar.gatech.edu"
