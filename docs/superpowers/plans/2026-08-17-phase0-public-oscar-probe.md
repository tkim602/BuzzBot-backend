# Phase 0 Public OSCAR Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded, deterministic probe that proves whether a small public OSCAR schedule sample is reachable and parseable before any bulk collection is allowed.

**Architecture:** A reusable probe core enforces request limits and safe response metadata. An OSCAR-specific provider builds the public listing URL and parses a small schedule sample. A CLI writes an ignored JSON report and sanitized raw-body snapshot; it never calls an LLM, database, embedding service, or authenticated GT endpoint.

**Tech Stack:** Python 3.11, stdlib dataclasses/enum/hashlib/json/argparse, existing `httpx`, existing `lxml`, pytest.

## Global Constraints

- Do not modify the user's existing uncommitted files: `docs/ingestion.md`, `eval/pipeline_phase1_eval_results.json`, `ingestion/discover.py`, `ingestion/run_ingestion.py`, `ingestion/sources.yaml`, `ingestion/crawl_all.py`, or `ingestion/discover_all_gt.py`.
- Default probe budget: 5 HTTP requests, 20 parsed records, 15-second timeout, one retry only for transport errors or 5xx.
- A single HTTP 429 returns `RATE_LIMITED`; do not retry it during the probe.
- Authentication redirects return `AUTH_REQUIRED`; do not follow into or use GT credentials.
- Persist only response body, source URL, final URL, status code, content type, fetch timestamp, SHA-256, and redirect URLs.
- Never persist cookies, `Set-Cookie`, authorization headers, session identifiers, CSRF values, or transient request tokens.
- No LLM, embeddings, LangGraph, database, or OpenAI API key is used in this phase.
- Write the failing test first and verify the expected failure before production code.
- Use current installed dependencies; add no package.

---

## File Map

- Create `ingestion/probes/__init__.py`: public probe types exported for callers.
- Create `ingestion/probes/core.py`: statuses, budgets, bounded HTTP session, result serialization, and safe snapshot writing.
- Create `ingestion/probes/oscar.py`: public OSCAR URL construction, deterministic HTML parsing, and probe classification.
- Create `ingestion/probes/cli.py`: command-line entry point and artifact paths.
- Create `tests/fixtures/oscar_schedule_sample.html`: minimal sanitized OSCAR schedule fixture with one timed and one TBA section.
- Create `tests/test_probe_core.py`: request-budget, retry, rate-limit, and secret-redaction behavior.
- Create `tests/test_oscar_probe.py`: listing parser and provider classification behavior.
- Create `tests/test_probe_cli.py`: report and raw-snapshot artifact behavior.
- Create `docs/probes.md`: bounded probe commands, statuses, and safety behavior.

---

### Task 1: Bounded Probe Core

**Files:**
- Create: `ingestion/probes/__init__.py`
- Create: `ingestion/probes/core.py`
- Test: `tests/test_probe_core.py`

**Interfaces:**
- Produces: `ProbeStatus`, `ProbeBudget`, `ProbeHttpResponse`, `ProbeResult`, `ProbeSession`, `write_probe_artifacts`.
- `ProbeSession.get(url: str) -> ProbeHttpResponse` counts every network attempt, retries only one transport/5xx failure, and never retries 429.
- `write_probe_artifacts(result: ProbeResult, response: ProbeHttpResponse | None, output_dir: Path) -> tuple[Path, Path | None]` writes safe JSON and optional raw body.

- [ ] **Step 1: Write failing request-policy tests**

Create `tests/test_probe_core.py` with focused async tests using `httpx.MockTransport`:

```python
from pathlib import Path

import httpx
import pytest

from ingestion.probes.core import (
    ProbeBudget,
    ProbeHttpResponse,
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

    budget = ProbeBudget(max_requests=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        session = ProbeSession(client, budget)
        await session.get("https://example.edu/one")
        with pytest.raises(RuntimeError, match="probe request budget exhausted"):
            await session.get("https://example.edu/two")


def test_artifact_metadata_excludes_sensitive_headers(tmp_path: Path):
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
    response = ProbeHttpResponse(
        source_url="https://example.edu/sample",
        final_url="https://example.edu/sample",
        status_code=200,
        content_type="text/html",
        body="<html>public</html>",
        fetched_at="2026-08-17T00:00:00+00:00",
        sha256="0" * 64,
    )
    report_path, body_path = write_probe_artifacts(result, response, tmp_path)

    persisted = report_path.read_text() + (body_path.read_text() if body_path else "")
    assert "Set-Cookie" not in persisted
    assert "Authorization" not in persisted
    assert "session-secret" not in persisted
```

The persistable response type deliberately has no arbitrary header dictionary. This makes sensitive-header persistence impossible by construction.

- [ ] **Step 2: Run the core tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_probe_core.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'ingestion.probes'`.

- [ ] **Step 3: Implement the minimal core**

Create frozen dataclasses and the bounded session in `ingestion/probes/core.py`:

```python
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
```

`ProbeSession.get` must increment the request count before every attempt, use the supplied `httpx.AsyncClient`, retry once only on `httpx.TransportError` or status `>= 500`, and return immediately for 429 or any other response. Construct `ProbeHttpResponse` from an allowlist rather than copying response headers.

`write_probe_artifacts` writes `<provider>-<timestamp>.json` plus `<provider>-<sha256>.html` when a body exists. The JSON payload is `{**asdict(result), "status": result.status.value, "response": safe_response_metadata}` where `safe_response_metadata` is constructed from the allowlisted response fields and excludes `body`. Use `json.dumps(..., default=str, indent=2)` and `Path.write_text`; `artifacts/` is already ignored.

Export the public types from `ingestion/probes/__init__.py`.

- [ ] **Step 4: Run the core tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_probe_core.py -q
```

Expected: all core tests pass.

- [ ] **Step 5: Commit the bounded core**

```bash
git add ingestion/probes/__init__.py ingestion/probes/core.py tests/test_probe_core.py
git commit -m "feat: add bounded ingestion probe core"
```

---

### Task 2: Public OSCAR Parser and Probe

**Files:**
- Create: `ingestion/probes/oscar.py`
- Create: `tests/fixtures/oscar_schedule_sample.html`
- Test: `tests/test_oscar_probe.py`

**Interfaces:**
- Consumes: `ProbeBudget`, `ProbeSession`, `ProbeResult`, `ProbeStatus` from Task 1.
- Produces: `OscarMeetingSample`, `OscarSectionSample`, `build_listing_url`, `parse_schedule_listing`, `probe_oscar`.
- `probe_oscar(session: ProbeSession, term: str, subject: str, course: str) -> tuple[ProbeResult, ProbeHttpResponse | None]` performs one public listing request.

- [ ] **Step 1: Create a sanitized OSCAR fixture**

Create `tests/fixtures/oscar_schedule_sample.html` containing the real table shape without email addresses:

```html
<table class="datadisplaytable" summary="This layout table is used to present the sections found">
  <caption class="captiontext">Sections Found</caption>
  <tr><th class="ddtitle"><a href="/bprod/detail?term_in=202608&amp;crn_in=90427">Natural Language - 90427 - CS 7650 - A</a></th></tr>
  <tr><td class="dddefault">
    <span class="fieldlabeltext">Associated Term: </span>Fall 2026<br>
    Georgia Tech-Atlanta * Campus<br>
    Lecture* Schedule Type<br>
    3.000 Credits<br>
    <table class="datadisplaytable" summary="This table lists the scheduled meeting times and assigned instructors for this class..">
      <caption class="captiontext">Scheduled Meeting Times</caption>
      <tr><th>Type</th><th>Time</th><th>Days</th><th>Where</th><th>Date Range</th><th>Schedule Type</th><th>Instructors</th></tr>
      <tr><td>Class</td><td>3:30 pm - 4:45 pm</td><td>MW</td><td>Paper Tricentennial 109</td><td>Aug 24, 2026 - Dec 17, 2026</td><td>Lecture*</td><td>Kartik Goyal (P)</td></tr>
    </table>
  </td></tr>
  <tr><th class="ddtitle"><a href="/bprod/detail?term_in=202608&amp;crn_in=89627">Natural Language - 89627 - CS 7650 - O01</a></th></tr>
  <tr><td class="dddefault">
    <span class="fieldlabeltext">Associated Term: </span>Fall 2026<br>
    Online Campus<br>Lecture* Schedule Type<br>3.000 Credits<br>
    <table class="datadisplaytable"><caption class="captiontext">Scheduled Meeting Times</caption>
      <tr><th>Type</th><th>Time</th><th>Days</th><th>Where</th><th>Date Range</th><th>Schedule Type</th><th>Instructors</th></tr>
      <tr><td>Class</td><td>TBA</td><td></td><td>TBA</td><td>Aug 24, 2026 - Dec 17, 2026</td><td>Lecture*</td><td>Mark O Riedl (P)</td></tr>
    </table>
  </td></tr>
</table>
```

- [ ] **Step 2: Write failing parser and classification tests**

Create `tests/test_oscar_probe.py`:

```python
from pathlib import Path

import httpx
import pytest

from ingestion.probes.core import ProbeBudget, ProbeSession, ProbeStatus
from ingestion.probes.oscar import build_listing_url, parse_schedule_listing, probe_oscar


FIXTURE = Path("tests/fixtures/oscar_schedule_sample.html")


def test_parse_schedule_listing_extracts_structured_fields():
    sections = parse_schedule_listing(FIXTURE.read_text(), max_records=20)

    assert len(sections) == 2
    assert sections[0].crn == "90427"
    assert sections[0].subject == "CS"
    assert sections[0].course == "7650"
    assert sections[0].section == "A"
    assert sections[0].credits == 3.0
    assert sections[0].meetings[0].time == "3:30 pm - 4:45 pm"
    assert sections[0].meetings[0].days == "MW"
    assert sections[0].meetings[0].location == "Paper Tricentennial 109"
    assert sections[0].meetings[0].instructor == "Kartik Goyal"
    assert sections[1].meetings[0].time == "TBA"


def test_build_listing_url_encodes_query():
    url = build_listing_url("202608", "CS", "7650")
    assert "term_in=202608" in url
    assert "subj_in=CS" in url
    assert "crse_in=7650" in url


@pytest.mark.asyncio
async def test_probe_classifies_public_listing_as_ready():
    html = FIXTURE.read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request, headers={"Content-Type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result, _ = await probe_oscar(ProbeSession(client, ProbeBudget()), "202608", "CS", "7650")

    assert result.status is ProbeStatus.READY
    assert result.public_access is True
    assert result.parsed_records == 2


@pytest.mark.asyncio
async def test_probe_classifies_429_without_retry():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, request=request, headers={"Retry-After": "60"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result, _ = await probe_oscar(ProbeSession(client, ProbeBudget()), "202608", "CS", "7650")

    assert calls == 1
    assert result.status is ProbeStatus.RATE_LIMITED


@pytest.mark.asyncio
async def test_probe_rejects_login_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "sso.gatech.edu":
            return httpx.Response(200, text="login", request=request)
        return httpx.Response(302, headers={"Location": "https://sso.gatech.edu/login"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result, _ = await probe_oscar(ProbeSession(client, ProbeBudget()), "202608", "CS", "7650")

    assert result.status is ProbeStatus.AUTH_REQUIRED
```

- [ ] **Step 3: Run the OSCAR tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_oscar_probe.py -q
```

Expected: import fails because `ingestion.probes.oscar` does not exist.

- [ ] **Step 4: Implement the OSCAR provider minimally**

In `ingestion/probes/oscar.py`:

- Build only the official public listing URL `https://oscar.gatech.edu/bprod/bwckctlg.p_disp_listcrse` using `urllib.parse.urlencode`.
- Parse only the main table whose caption is `Sections Found`.
- Pair each direct `th.ddtitle` row with its following detail row.
- Parse title headers with a compiled regex for `title - CRN - SUBJECT COURSE - SECTION`.
- Extract credits with `r"(\d+(?:\.\d+)?)\s+Credits"`.
- Zip meeting-table headers and cells instead of relying on fixed cell indices.
- Strip `(P)` and whitespace from instructor display names; ignore email links.
- Treat `TBA` as a valid explicit value.
- Stop after `max_records`.
- Classify 429 as `RATE_LIMITED`, 401/403 as `AUTH_REQUIRED`, login/SSO redirect chains as `AUTH_REQUIRED`, non-200 as `UNAVAILABLE`, empty/incompatible HTML as `PARSE_FAILED`, and valid rows as `READY`.

Use frozen dataclasses:

```python
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
```

- [ ] **Step 5: Run the OSCAR tests and full baseline**

Run:

```bash
python3 -m pytest tests/test_oscar_probe.py -q
python3 -m pytest tests/ -q
```

Expected: new OSCAR tests pass and existing 66 tests remain green.

- [ ] **Step 6: Commit the OSCAR provider**

```bash
git add ingestion/probes/oscar.py tests/fixtures/oscar_schedule_sample.html tests/test_oscar_probe.py
git commit -m "feat: probe public OSCAR schedule listings"
```

---

### Task 3: Probe CLI and Safe Artifacts

**Files:**
- Create: `ingestion/probes/cli.py`
- Test: `tests/test_probe_cli.py`
- Create: `docs/probes.md`

**Interfaces:**
- Consumes: `ProbeBudget`, `ProbeSession`, `write_probe_artifacts`, and `probe_oscar`.
- Produces: `async run_oscar_probe(term: str, subject: str, course: str, output_dir: Path, transport: httpx.AsyncBaseTransport | None = None) -> ProbeResult` and `main() -> None`.
- CLI: `python3 -m ingestion.probes.cli oscar --term 202608 --subject CS --course 7650`.

- [ ] **Step 1: Write a failing CLI artifact test**

Create `tests/test_probe_cli.py` using `httpx.MockTransport` and a temporary output directory:

```python
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
    assert "session-secret" not in report + body
```

- [ ] **Step 2: Run the CLI test and verify RED**

Run:

```bash
python3 -m pytest tests/test_probe_cli.py -q
```

Expected: import fails because `ingestion.probes.cli` does not exist.

- [ ] **Step 3: Implement the CLI**

Use `argparse` with a required `oscar` subcommand and required `--term`, `--subject`, and `--course`. Default `--output-dir` to `artifacts/probes`. Construct one `httpx.AsyncClient` with the 15-second timeout, the existing BuzzBot user agent, and `follow_redirects=True`. Pass optional test transport through unchanged.

Print the JSON report to stdout and exit with:

- `0` for `READY`
- `2` for `RATE_LIMITED`, `AUTH_REQUIRED`, `UNAVAILABLE`, or `PARSE_FAILED`

Do not read `.env`, initialize a database, or import LLM/indexing modules.

- [ ] **Step 4: Document exact usage**

Create `docs/probes.md` with:

````markdown
# Source Probes

Run a small public OSCAR check before any schedule synchronization:

```bash
python3 -m ingestion.probes.cli oscar --term 202608 --subject CS --course 7650
```

The command makes at most five HTTP attempts, parses at most twenty sections, stops immediately on 429 or authentication, and writes only safe report metadata plus the public response body under ignored `artifacts/probes/`.

`READY` proves only that the sample is publicly reachable and structurally parseable. It does not authorize bulk collection; the subsequent sync must still enforce rate limits, staging, coverage validation, and transactional publishing.
````

- [ ] **Step 5: Run focused and full tests**

Run:

```bash
python3 -m pytest tests/test_probe_core.py tests/test_oscar_probe.py tests/test_probe_cli.py -q
python3 -m pytest tests/ -q
```

Expected: all probe tests and the full suite pass.

- [ ] **Step 6: Commit the CLI and docs**

```bash
git add ingestion/probes/cli.py tests/test_probe_cli.py docs/probes.md
git commit -m "feat: add safe OSCAR probe command"
```

---

### Task 4: Live Phase 0 Verification

**Files:**
- No tracked file changes required.
- Creates ignored files under `artifacts/probes/`.

**Interfaces:**
- Consumes the completed CLI from Task 3.
- Produces a real `ProbeResult` and sanitized snapshot for the selected OSCAR sample.

- [ ] **Step 1: Run one bounded live probe**

```bash
python3 -m ingestion.probes.cli oscar --term 202608 --subject CS --course 7650
```

Expected on the currently verified public page: exit code `0`, status `READY`, one request, at least one and no more than twenty parsed records, `public_access: true`, and required fields present.

- [ ] **Step 2: Validate artifact safety and limits**

```bash
python3 - <<'PY'
import json
from pathlib import Path

reports = sorted(Path("artifacts/probes").glob("*.json"))
assert reports, "probe report missing"
report = json.loads(reports[-1].read_text())
assert report["requests_used"] <= 5
assert 1 <= report["parsed_records"] <= 20
persisted = reports[-1].read_text()
for forbidden in ("Set-Cookie", "Authorization", "session-secret", "OPENAI_API_KEY"):
    assert forbidden not in persisted
print(report["status"], report["parsed_records"], report["requests_used"])
PY
```

Expected: `READY <count> <requests>` with no assertion failure.

- [ ] **Step 3: Run static checks only on new files**

```bash
python3 -m ruff check ingestion/probes tests/test_probe_core.py tests/test_oscar_probe.py tests/test_probe_cli.py
python3 -m mypy ingestion/probes
```

Expected: no errors in the newly added probe modules. Existing repository-wide lint/type debt is outside this phase.

- [ ] **Step 4: Record the Phase 0 decision**

Report, without committing ignored response artifacts:

- exact probe status and reason
- request and parsed-record counts
- final public URL and response SHA-256
- whether required fields were present
- whether Phase 1 structured schedule ingestion is unblocked

If the result is not `READY`, stop. Do not increase the budget or try adjacent endpoints automatically.
