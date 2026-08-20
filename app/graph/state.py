from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

GraphIntent = Literal[
    "course_schedule",
    "course_details",
    "registration_calendar",
    "policy",
]


class EvidenceItem(TypedDict):
    kind: Literal["schedule", "document"]
    text: str
    url: str
    title: str | None
    fetched_at: str | None
    source: str
    metadata: dict[str, object]


class CitationItem(TypedDict):
    url: str
    title: str | None
    fetched_at: str | None
    quote: str


class AgentState(TypedDict):
    query: str
    history: NotRequired[list[dict[str, str]]]
    user_term: NotRequired[str | None]
    intent: NotRequired[GraphIntent]
    subject: NotRequired[str | None]
    course_number: NotRequired[str | None]
    term_code: NotRequired[str | None]
    needs_clarification: NotRequired[bool]
    clarification: NotRequired[str]
    retry_count: NotRequired[int]
    evidence: NotRequired[list[EvidenceItem]]
    evidence_valid: NotRequired[bool]
    answer: NotRequired[str]
    citations: NotRequired[list[CitationItem]]
    confidence: NotRequired[float]
    notes: NotRequired[list[str]]
    answer_valid: NotRequired[bool]
