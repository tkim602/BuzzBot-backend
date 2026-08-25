from app.api.freshness import evidence_freshness_as_of


def _evidence(kind: str, fetched_at: str | None) -> dict[str, object]:
    return {
        "kind": kind,
        "text": "official evidence",
        "url": "https://gatech.edu/official",
        "title": "Official source",
        "fetched_at": fetched_at,
        "source": "oscar" if kind == "schedule" else "gt-registrar",
        "metadata": {},
    }


def test_schedule_freshness_uses_snapshot_timestamp_not_response_time():
    assert evidence_freshness_as_of([_evidence("schedule", "2026-08-20T01:02:03Z")]) == (
        "2026-08-20T01:02:03+00:00"
    )


def test_document_freshness_uses_retrieved_evidence_timestamp():
    assert evidence_freshness_as_of([_evidence("document", "2026-08-18T10:00:00+00:00")]) == (
        "2026-08-18T10:00:00+00:00"
    )


def test_missing_or_invalid_freshness_returns_none():
    assert evidence_freshness_as_of([_evidence("document", None)]) is None
    assert evidence_freshness_as_of([_evidence("document", "not-a-date")]) is None


def test_mixed_evidence_uses_oldest_available_timestamp():
    assert (
        evidence_freshness_as_of(
            [
                _evidence("schedule", "2026-08-24T00:00:00+00:00"),
                _evidence("document", "2026-08-19T00:00:00+00:00"),
            ]
        )
        == "2026-08-19T00:00:00+00:00"
    )
