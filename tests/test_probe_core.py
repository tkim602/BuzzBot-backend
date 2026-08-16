from pathlib import Path

import httpx
import pytest

from ingestion.probes.core import (
    ProbeBudget,
    ProbeResult,
    ProbeSession,
    ProbeStatus,
    write_probe_artifacts,
)


@pytest.mark.asyncio
async def test_429_is_not_retried():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "120"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await ProbeSession(client, ProbeBudget()).get("https://example.edu/sample")

    assert calls == 1
    assert response.status_code == 429
    assert response.retry_after == "120"


@pytest.mark.asyncio
async def test_one_5xx_retry_is_allowed():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200, text="ok", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await ProbeSession(client, ProbeBudget()).get("https://example.edu/sample")

    assert calls == 2
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_request_budget_is_enforced():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        session = ProbeSession(client, ProbeBudget(max_requests=1))
        await session.get("https://example.edu/one")
        with pytest.raises(RuntimeError, match="probe request budget exhausted"):
            await session.get("https://example.edu/two")


@pytest.mark.asyncio
async def test_artifacts_exclude_unapproved_response_headers(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html>public</html>",
            request=request,
            headers={
                "Content-Type": "text/html",
                "Set-Cookie": "session-secret",
                "Authorization": "bearer-secret",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await ProbeSession(client, ProbeBudget()).get("https://example.edu/sample")

    result = ProbeResult(
        provider="public-oscar",
        status=ProbeStatus.READY,
        reachable=True,
        public_access=True,
        parsed_records=1,
        required_fields_present=True,
        requests_used=1,
        latency_ms=10,
    )
    report_path, body_path = write_probe_artifacts(result, response, tmp_path)

    assert body_path is not None
    persisted = report_path.read_text() + body_path.read_text()
    assert "session-secret" not in persisted
    assert "bearer-secret" not in persisted
    assert "Set-Cookie" not in persisted
    assert "Authorization" not in persisted
