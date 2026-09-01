"""Evidence-derived response freshness."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime


def evidence_freshness_as_of(evidence: Sequence[Mapping[str, object]]) -> str | None:
    """Return the oldest evidence timestamp, or None when the set is not fully dated."""
    if not evidence:
        return None
    timestamps: list[datetime] = []
    for item in evidence:
        value = item.get("fetched_at")
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        timestamps.append(parsed.astimezone(UTC))
    return min(timestamps).isoformat()
