# Academic Calendar XHR Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace false-success static-page extraction with validated ingestion of the official Georgia Tech Academic Calendar XHR data.

**Architecture:** The existing one-request document probe discovers the selected current academic year. A focused calendar module builds the official proxy request, validates and normalizes its JSON, and returns deterministic document text. The existing document store, chunker, embedder, and canonical URL replacement path remain unchanged.

**Tech Stack:** Python 3.11, httpx, lxml, SQLAlchemy/PostgreSQL, pytest.

---

### Task 1: Discover and validate the official calendar payload

**Files:**
- Create: `ingestion/documents/calendar.py`
- Modify: `ingestion/documents/probe.py`
- Test: `tests/test_academic_calendar.py`
- Test: `tests/test_document_probe.py`

- [x] **Step 1: Write failing discovery and parser tests**

```python
def test_selected_academic_year_is_discovered():
    assert selected_academic_year(PAGE_HTML) == "2026-2027"

def test_calendar_payload_becomes_deterministic_text():
    document = parse_calendar_payload("2026-2027", VALID_PAYLOAD)
    assert document.event_count == 25
    assert "Semester: Fall 2026" in document.text
    assert "Event: Registration opens." in document.text

def test_too_small_calendar_payload_is_rejected():
    with pytest.raises(CalendarPayloadError, match="TOO_FEW_EVENTS"):
        parse_calendar_payload("2026-2027", {"data": []})
```

- [x] **Step 2: Run tests and verify RED**

Run: `PYTHONPATH=$PWD python3 -m pytest -q tests/test_academic_calendar.py tests/test_document_probe.py`

Expected: test collection fails because `ingestion.documents.calendar` does not exist.

- [x] **Step 3: Implement the minimum calendar module and probe edition field**

```python
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit, urlunsplit

from lxml import html as lxml_html

MIN_CALENDAR_EVENTS = 25
SEMESTERS = {"2": "Spring", "5A": "Summer", "5E": "Summer-Early", "5F": "Summer-Full", "5L": "Summer-Late", "5M": "Summer-May", "8": "Fall"}

@dataclass(frozen=True)
class CalendarDocument:
    edition: str
    text: str
    event_count: int

class CalendarPayloadError(ValueError):
    pass

def selected_academic_year(html: str) -> str | None:
    root = lxml_html.fromstring(html)
    values = root.xpath("//select[@id='academic-year']/option[@selected]/@value")
    return str(values[0]).strip() if values else None

def calendar_request_url(seed_url: str, edition: str) -> str:
    parsed = urlsplit(seed_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/calevents/proxy", urlencode({"year": edition, "status": "current"}), ""))

def parse_calendar_payload(edition: str, payload: object) -> CalendarDocument:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise CalendarPayloadError("INVALID_JSON_SHAPE")
    rows = payload["data"]
    if len(rows) < MIN_CALENDAR_EVENTS:
        raise CalendarPayloadError("TOO_FEW_EVENTS")
    blocks = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or any(not str(row.get(key, "")).strip() for key in ("id", "date", "semester", "year", "category", "event")):
            raise CalendarPayloadError("MISSING_REQUIRED_FIELD")
        event = " ".join(lxml_html.fragment_fromstring(str(row["event"]), create_parent="div").text_content().split())
        semester = SEMESTERS.get(str(row["semester"]), str(row["semester"]))
        block = f"Semester: {semester} {row['year']}\nCategory: {row['category']}\nDate: {row['date']}, {row['year']}\nEvent: {event}"
        blocks.append((int(row.get("weight", index)), str(row["id"]), block))
    text = f"Georgia Tech Academic Calendar {edition}\n\n" + "\n\n".join(block for _, _, block in sorted(blocks))
    return CalendarDocument(edition, text, len(rows))
```

Add `edition: str | None = None` to `DocumentProbeResult`. For `academic_calendar`, return `PARSE_FAILED/CALENDAR_YEAR_NOT_FOUND` unless `selected_academic_year` finds the selected `20xx-20xx` option.

- [x] **Step 4: Run focused tests and verify GREEN**

Run: `PYTHONPATH=$PWD python3 -m pytest -q tests/test_academic_calendar.py tests/test_document_probe.py`

Expected: all focused tests pass.

### Task 2: Route calendar sync through the validated XHR collector

**Files:**
- Modify: `ingestion/documents/sync.py`
- Modify: `tests/test_document_sync.py`

- [x] **Step 1: Write failing sync tests**

```python
async def test_calendar_sync_fetches_proxy_with_public_xhr_headers():
    result = await sync_document_source(calendar_source, sessions, embed, transport)
    assert requested_urls == [calendar_page, expected_proxy_url]
    assert proxy_request.headers["X-Requested-With"] == "XMLHttpRequest"
    assert result.outcome is DocumentSyncOutcome.INDEXED

async def test_invalid_calendar_payload_never_opens_database_or_embeds():
    result = await sync_document_source(calendar_source, forbidden_sessions, forbidden_embed, transport)
    assert result.outcome is DocumentSyncOutcome.EXTRACT_FAILED
```

- [x] **Step 2: Run tests and verify RED**

Run: `PYTHONPATH=$PWD python3 -m pytest -q tests/test_document_sync.py`

Expected: calendar sync still fetches and extracts the static seed page instead of the proxy JSON.

- [x] **Step 3: Implement the minimum source-specific fetch branch**

```python
if source.source_type == "academic_calendar":
    fetched, fetch_error = await _fetch_calendar_document(source, probe.edition, transport)
else:
    fetched, fetch_error = await _fetch_html_document(source, transport, headers)
```

The calendar branch performs exactly one request after the probe, applies public XHR headers, parses JSON, and maps validation failures to `EXTRACT_FAILED` before `_store_document` is called.

- [x] **Step 4: Run focused tests and verify GREEN**

Run: `PYTHONPATH=$PWD python3 -m pytest -q tests/test_document_sync.py tests/test_academic_calendar.py tests/test_document_probe.py`

Expected: all focused tests pass.

### Task 3: Verify the regression and live public endpoint

**Files:**
- Modify only if verification exposes a bug in the files above.

- [x] **Step 1: Run repository verification**

```bash
PYTHONPATH=$PWD python3 -m pytest -q
python3 -m ruff check ingestion/documents/calendar.py ingestion/documents/probe.py ingestion/documents/sync.py tests/test_academic_calendar.py tests/test_document_probe.py tests/test_document_sync.py
python3 -m mypy ingestion/documents
git diff --check
```

- [x] **Step 2: Run a no-database live probe**

```bash
PYTHONPATH=$PWD python3 -m ingestion.documents.cli probe --source gt-academic-calendar
```

Expected: `READY`, one request, and edition `2026-2027` in the compact result.

- [x] **Step 3: Inspect the final diff and report the manual resync command**

Run: `git diff --stat && git status --short`

The handoff must identify `make sync-doc source=gt-academic-calendar` as the command that replaces the stale 11-token calendar document. Do not run paid embedding during verification.
