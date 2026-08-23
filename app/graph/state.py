from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

GraphIntent = Literal[
    "course_schedule",
    "course_details",
    "registration_calendar",
    "policy",
]
ScheduleQueryType = Literal[
    "offering",
    "sections",
    "crns",
    "instructors",
    "meeting",
    "location",
    "online_availability",
    "general_schedule",
]
EvidenceValidationReason = Literal[
    "NO_EVIDENCE",
    "MISSING_TEXT",
    "INVALID_URL",
    "EXPIRED_SCHEDULE",
    "MISSING_AUTHORITY",
    "VALID",
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
    page: NotRequired[int]


class AgentState(TypedDict):
    query: str
    history: NotRequired[list[dict[str, str]]]
    user_term: NotRequired[str | None]
    intent: NotRequired[GraphIntent]
    subject: NotRequired[str | None]
    course_number: NotRequired[str | None]
    term_code: NotRequired[str | None]
    schedule_query_type: NotRequired[ScheduleQueryType]
    needs_clarification: NotRequired[bool]
    clarification: NotRequired[str]
    retry_count: NotRequired[int]
    evidence: NotRequired[list[EvidenceItem]]
    evidence_valid: NotRequired[bool]
    evidence_validation_reason: NotRequired[EvidenceValidationReason]
    retrieval_top_k: NotRequired[int]
    returned_evidence_count: NotRequired[int]
    returned_urls: NotRequired[list[str]]
    retrieval_scores: NotRequired[list[float]]
    retrieval_methods: NotRequired[list[str]]
    answer: NotRequired[str]
    citations: NotRequired[list[CitationItem]]
    confidence: NotRequired[float]
    notes: NotRequired[list[str]]
    binary_verdict: NotRequired[Literal["TRUE", "FALSE"]]
    grounding_valid: NotRequired[bool]
    claims_supported: NotRequired[bool]
    polarity_consistent: NotRequired[bool]
    answer_nonempty: NotRequired[bool]
    answer_valid: NotRequired[bool]
    abstain_reason: NotRequired[
        Literal["CLARIFICATION_REQUIRED", "NO_VALID_EVIDENCE", "ANSWER_VALIDATION_FAILED"]
    ]
