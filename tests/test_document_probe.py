import httpx
import pytest

from ingestion.documents.probe import DocumentProbeStatus, probe_document_source
from ingestion.documents.registry import DocumentSource


def _source() -> DocumentSource:
    return DocumentSource(
        name="gt-registrar",
        source_type="official_policy",
        authority="registrar",
        allowed_roots=("https://registrar.gatech.edu/",),
        seed_urls=("https://registrar.gatech.edu/registration",),
        max_urls=5,
    )


def _calendar_source() -> DocumentSource:
    return DocumentSource(
        name="gt-academic-calendar",
        source_type="academic_calendar",
        authority="academic_calendar",
        allowed_roots=("https://registrar.gatech.edu/",),
        seed_urls=("https://registrar.gatech.edu/current-academic-calendar",),
        max_urls=5,
    )


@pytest.mark.asyncio
async def test_probe_uses_one_public_request_and_accepts_document_body():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            text="<html><title>Registration</title><body>"
            + "official policy " * 20
            + "</body></html>",
            headers={"Content-Type": "text/html"},
            request=request,
        )

    result = await probe_document_source(_source(), httpx.MockTransport(handler))

    assert calls == result.requests_used == 1
    assert result.status is DocumentProbeStatus.READY
    assert result.title == "Registration"


@pytest.mark.asyncio
async def test_calendar_probe_discovers_current_academic_year_in_one_request():
    page = """
    <html><title>Current Academic Calendar</title><body>
      <select id="academic-year">
        <option value="2026-2027" selected>2026-2027</option>
      </select>
      Current Academic Calendar and important Georgia Tech dates.
      Registration, classes, exams, grades, graduation, holidays, and payment deadlines.
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=page,
            headers={"Content-Type": "text/html"},
            request=request,
        )

    result = await probe_document_source(_calendar_source(), httpx.MockTransport(handler))

    assert result.status is DocumentProbeStatus.READY
    assert result.requests_used == 1
    assert result.edition == "2026-2027"


@pytest.mark.asyncio
async def test_calendar_probe_rejects_shell_without_selected_academic_year():
    page = (
        "<html><title>Current Academic Calendar</title><body>"
        + "calendar shell " * 20
        + "</body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=page,
            headers={"Content-Type": "text/html"},
            request=request,
        )

    result = await probe_document_source(_calendar_source(), httpx.MockTransport(handler))

    assert result.status is DocumentProbeStatus.PARSE_FAILED
    assert result.reason == "CALENDAR_YEAR_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "headers", "expected"),
    [
        (429, {}, DocumentProbeStatus.RATE_LIMITED),
        (302, {"Location": "https://login.gatech.edu/"}, DocumentProbeStatus.AUTH_REQUIRED),
        (302, {"Location": "https://evil.example/"}, DocumentProbeStatus.DISALLOWED_REDIRECT),
    ],
)
async def test_probe_stops_before_rate_limit_auth_or_external_redirect(
    status_code: int,
    headers: dict[str, str],
    expected: DocumentProbeStatus,
):
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host or "")
        return httpx.Response(status_code, headers=headers, request=request)

    result = await probe_document_source(_source(), httpx.MockTransport(handler))

    assert result.status is expected
    assert result.requests_used == 1
    assert hosts == ["registrar.gatech.edu"]
