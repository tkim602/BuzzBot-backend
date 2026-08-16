from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import httpx


class ProbeStatus(StrEnum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    PARSE_FAILED = "PARSE_FAILED"


@dataclass(frozen=True)
class ProbeBudget:
    max_requests: int = 5
    max_records: int = 20
    timeout_seconds: float = 15.0


@dataclass(frozen=True)
class ProbeHttpResponse:
    source_url: str
    final_url: str
    status_code: int
    content_type: str | None
    body: str
    fetched_at: str
    sha256: str
    redirect_urls: tuple[str, ...] = ()
    retry_after: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    provider: str
    status: ProbeStatus
    reachable: bool
    public_access: bool
    parsed_records: int
    required_fields_present: bool
    requests_used: int
    latency_ms: int
    reason: str | None = None
    retry_after_seconds: int | None = None


class ProbeSession:
    def __init__(self, client: httpx.AsyncClient, budget: ProbeBudget):
        self.client = client
        self.budget = budget
        self.requests_used = 0
        self.started_at = time.monotonic()

    @property
    def latency_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    async def get(self, url: str) -> ProbeHttpResponse:
        last_error: str | None = None
        for attempt in range(2):
            if self.requests_used >= self.budget.max_requests:
                raise RuntimeError("probe request budget exhausted")
            self.requests_used += 1

            try:
                response = await self.client.get(url)
            except httpx.TransportError as exc:
                last_error = str(exc)
                if attempt == 0:
                    continue
                return _error_response(url, last_error)

            if response.status_code >= 500 and attempt == 0:
                continue
            return _safe_response(url, response)

        return _error_response(url, last_error or "probe request failed")


def _safe_response(source_url: str, response: httpx.Response) -> ProbeHttpResponse:
    body = response.text
    content_type = response.headers.get("Content-Type")
    if content_type:
        content_type = content_type.split(";", 1)[0].strip()
    return ProbeHttpResponse(
        source_url=source_url,
        final_url=str(response.url),
        status_code=response.status_code,
        content_type=content_type,
        body=body,
        fetched_at=datetime.now(UTC).isoformat(),
        sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        redirect_urls=tuple(str(item.url) for item in response.history),
        retry_after=response.headers.get("Retry-After"),
    )


def _error_response(source_url: str, error: str) -> ProbeHttpResponse:
    return ProbeHttpResponse(
        source_url=source_url,
        final_url=source_url,
        status_code=0,
        content_type=None,
        body="",
        fetched_at=datetime.now(UTC).isoformat(),
        sha256=hashlib.sha256(b"").hexdigest(),
        error=error,
    )


def write_probe_artifacts(
    result: ProbeResult,
    response: ProbeHttpResponse | None,
    output_dir: Path,
) -> tuple[Path, Path | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = output_dir / f"{result.provider}-{timestamp}.json"
    body_path: Path | None = None

    response_metadata = None
    if response:
        response_metadata = {
            "source_url": response.source_url,
            "final_url": response.final_url,
            "status_code": response.status_code,
            "content_type": response.content_type,
            "fetched_at": response.fetched_at,
            "sha256": response.sha256,
            "redirect_urls": response.redirect_urls,
            "retry_after": response.retry_after,
            "error": response.error,
        }
        if response.body:
            body_path = output_dir / f"{result.provider}-{response.sha256}.html"
            body_path.write_text(response.body, encoding="utf-8")

    payload = {**asdict(result), "status": result.status.value, "response": response_metadata}
    report_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return report_path, body_path
