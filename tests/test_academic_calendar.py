import pytest

from ingestion.chunk import chunk_text
from ingestion.documents.calendar import (
    CalendarPayloadError,
    calendar_request_url,
    parse_calendar_payload,
    selected_academic_year,
)


def _rows(count: int = 25) -> list[dict[str, object]]:
    return [
        {
            "id": str(index),
            "date": "August 17 (Mon)",
            "semester": "8",
            "year": "2026",
            "category": "Registration",
            "event": "<p>Registration <strong>opens</strong>.</p>",
            "weight": index,
        }
        for index in range(count)
    ]


def test_selected_academic_year_is_discovered_from_official_select():
    page = """
    <select id="academic-year">
      <option value="2025-2026">2025-2026</option>
      <option value="2026-2027" selected="selected">2026-2027</option>
    </select>
    """

    assert selected_academic_year(page) == "2026-2027"


def test_selected_academic_year_rejects_missing_or_invalid_edition():
    assert selected_academic_year("<html><body>No calendar selector</body></html>") is None
    assert (
        selected_academic_year(
            '<select id="academic-year"><option selected value="next-year">Next</option></select>'
        )
        is None
    )


def test_calendar_request_url_stays_on_official_seed_origin():
    assert calendar_request_url(
        "https://registrar.gatech.edu/current-academic-calendar", "2026-2027"
    ) == ("https://registrar.gatech.edu/calevents/proxy?year=2026-2027&status=current")


def test_calendar_payload_becomes_deterministic_plain_text():
    rows = _rows()
    rows[0]["weight"] = 50
    rows[1]["weight"] = 1
    rows[1]["event"] = "<p>Registration <strong>opens</strong>.</p>"

    document = parse_calendar_payload("2026-2027", {"data": rows})

    assert document.edition == "2026-2027"
    assert document.event_count == 25
    assert document.title == "Georgia Tech Academic Calendar 2026-2027"
    assert "Semester: Fall 2026" in document.text
    assert "Date: August 17 (Mon), 2026" in document.text
    assert "Event: Registration opens." in document.text
    assert "<strong>" not in document.text
    assert document.text.index(" — Event 1") < document.text.index(" — Event 0")


def test_every_calendar_event_survives_chunking():
    document = parse_calendar_payload("2026-2027", {"data": _rows()})

    chunks = chunk_text(document.text, min_chunk_size=10)

    assert len(chunks) == document.event_count
    assert sum(chunk.text.count(" — Event ") for chunk in chunks) == document.event_count


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ([], "INVALID_JSON_SHAPE"),
        ({"events": _rows()}, "INVALID_JSON_SHAPE"),
        ({"data": _rows(24)}, "TOO_FEW_EVENTS"),
    ],
)
def test_calendar_payload_rejects_invalid_or_too_small_collections(payload, reason):
    with pytest.raises(CalendarPayloadError, match=reason):
        parse_calendar_payload("2026-2027", payload)


def test_calendar_payload_rejects_missing_required_event_fields():
    rows = _rows()
    rows[3]["category"] = ""

    with pytest.raises(CalendarPayloadError, match="MISSING_REQUIRED_FIELD"):
        parse_calendar_payload("2026-2027", {"data": rows})


def test_calendar_payload_rejects_event_html_without_text():
    rows = _rows()
    rows[3]["event"] = "<span></span>"

    with pytest.raises(CalendarPayloadError, match="EMPTY_EVENT_TEXT"):
        parse_calendar_payload("2026-2027", {"data": rows})
