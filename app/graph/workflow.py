from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.state import AgentState, CitationItem, EvidenceItem, GraphIntent
from app.graph.understanding import understand_query
from app.rag.answerer import generate_answer
from app.rag.grounding import check_claim_support, check_grounding, check_yes_no_consistency
from app.rag.retrieval import RetrievedChunk, get_query_embedding
from app.retrieval import (
    CourseDetailsQuery,
    CourseQuery,
    PolicyQuery,
    RegistrationCalendarQuery,
    lookup_course_details,
    lookup_course_offerings,
    lookup_registration_calendar,
    search_policy_docs,
)

EmbedQuery = Callable[[str], Awaitable[list[float]]]
DocumentAnswerer = Callable[[str, list[EvidenceItem], GraphIntent], Awaitable[dict[str, object]]]


@dataclass(frozen=True)
class WorkflowServices:
    session: AsyncSession
    embed_query: EmbedQuery = get_query_embedding
    answer_documents: DocumentAnswerer | None = None


def _numeric(value: object, default: float) -> float:
    return float(value) if isinstance(value, int | float) else default


def _document_item(evidence: Any) -> EvidenceItem:
    return EvidenceItem(
        kind="document",
        text=evidence.text,
        url=evidence.canonical_url,
        title=evidence.title,
        fetched_at=evidence.fetched_at,
        source=evidence.source_name,
        metadata={
            "source_type": evidence.source_type,
            "authority": evidence.authority,
            "edition": evidence.edition,
            "score": evidence.score,
            "retrieval_method": evidence.retrieval_method,
        },
    )


def _schedule_item(offering: Any) -> EvidenceItem:
    meeting_parts = []
    for meeting in offering.meetings:
        if meeting.is_tba:
            meeting_parts.append("TBA")
        else:
            meeting_parts.append(
                " ".join(
                    str(value)
                    for value in (
                        meeting.days,
                        meeting.start_time,
                        meeting.end_time,
                        meeting.building,
                        meeting.room,
                    )
                    if value is not None
                )
            )
    meetings = "; ".join(meeting_parts) or "meeting TBA"
    instructors = ", ".join(offering.instructors) or "instructor TBA"
    text = (
        f"{offering.subject} {offering.course_number} {offering.title}; "
        f"section {offering.section_code}; CRN {offering.crn}; {offering.campus}; "
        f"{offering.schedule_type}; {instructors}; {meetings}."
    )
    return EvidenceItem(
        kind="schedule",
        text=text,
        url=offering.source_url,
        title=f"{offering.subject} {offering.course_number} schedule",
        fetched_at=offering.data_as_of.isoformat(),
        source="oscar",
        metadata={
            "term_code": offering.term_code,
            "crn": offering.crn,
            "data_version_id": str(offering.data_version_id),
            "freshness": str(offering.freshness),
        },
    )


def _as_chunks(evidence: list[EvidenceItem]) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id=f"graph-{index}",
            url=item["url"],
            title=item["title"],
            chunk_text=item["text"],
            score=_numeric(item["metadata"].get("score"), 1.0),
            source_name=item["source"],
            fetched_at=item["fetched_at"],
            metadata_json=item["metadata"],
            method=str(item["metadata"].get("retrieval_method", item["kind"])),
        )
        for index, item in enumerate(evidence)
    ]


async def _default_document_answer(
    query: str,
    evidence: list[EvidenceItem],
    intent: GraphIntent,
) -> dict[str, object]:
    answer_intent = {
        "course_details": "catalog_course",
        "registration_calendar": "registrar_calendar",
        "policy": "policy",
    }.get(intent, "general")
    return cast(
        dict[str, object],
        await generate_answer(query, _as_chunks(evidence), intent=answer_intent),
    )


def _policy_source_types(query: str) -> tuple[str, ...]:
    lowered = query.lower()
    if "omscs" in lowered:
        return ("omscs_policy",)
    if any(
        cue in lowered
        for cue in (
            "admission",
            "apply",
            "application",
            "first-year",
            "first year",
            "early action",
            "common app",
            "recommendation",
            "intended major",
            "major selection",
        )
    ):
        return ("admissions",)
    return ()


def build_workflow(
    services: WorkflowServices,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph:
    async def understand_node(state: AgentState) -> dict[str, object]:
        return understand_query(state["query"], state.get("user_term"))

    async def retrieve_node(state: AgentState) -> dict[str, object]:
        intent = cast(GraphIntent, state["intent"])
        retry_count = state.get("retry_count", 0)
        top_k = 5 if retry_count == 0 else 8
        if intent == "course_schedule":
            offerings = await lookup_course_offerings(
                services.session,
                CourseQuery(
                    term_code=cast(str, state["term_code"]),
                    subject=cast(str, state["subject"]),
                    course_number=cast(str, state["course_number"]),
                ),
            )
            evidence = [_schedule_item(offering) for offering in offerings]
        else:
            embedding = await services.embed_query(state["query"])
            if intent == "course_details":
                documents = await lookup_course_details(
                    services.session,
                    CourseDetailsQuery(
                        cast(str, state["subject"]),
                        cast(str, state["course_number"]),
                        top_k=top_k,
                    ),
                    embedding,
                )
            elif intent == "registration_calendar":
                documents = await lookup_registration_calendar(
                    services.session,
                    RegistrationCalendarQuery(state["query"], top_k=top_k),
                    embedding,
                )
            else:
                documents = await search_policy_docs(
                    services.session,
                    PolicyQuery(
                        state["query"],
                        source_types=_policy_source_types(state["query"]),
                        top_k=top_k,
                    ),
                    embedding,
                )
            evidence = [_document_item(document) for document in documents]
        return {"evidence": evidence}

    async def validate_evidence_node(state: AgentState) -> dict[str, object]:
        evidence = state.get("evidence", [])
        valid = bool(evidence)
        for item in evidence:
            valid = valid and bool(item["text"].strip() and item["url"].startswith("https://"))
            if item["kind"] == "schedule":
                valid = valid and item["metadata"].get("freshness") != "EXPIRED"
            else:
                valid = valid and bool(item["metadata"].get("authority"))
        return {"evidence_valid": valid}

    async def prepare_retry_node(state: AgentState) -> dict[str, object]:
        return {
            "retry_count": state.get("retry_count", 0) + 1,
            "notes": [*state.get("notes", []), "Retrieval retried once with a wider limit."],
        }

    async def answer_node(state: AgentState) -> dict[str, object]:
        evidence = state.get("evidence", [])
        if state["intent"] == "course_schedule":
            citations = [
                CitationItem(
                    url=item["url"],
                    title=item["title"],
                    fetched_at=item["fetched_at"],
                    quote=item["text"],
                )
                for item in evidence
            ]
            return {
                "answer": "\n".join(item["text"] for item in evidence),
                "citations": citations,
                "confidence": 0.95,
            }

        answerer = services.answer_documents or _default_document_answer
        raw = await answerer(state["query"], evidence, cast(GraphIntent, state["intent"]))
        return {
            "answer": str(raw.get("answer", "")),
            "citations": cast(list[CitationItem], raw.get("citations", [])),
            "confidence": _numeric(raw.get("confidence"), 0.5),
            "notes": [*state.get("notes", []), *cast(list[str], raw.get("notes", []))],
        }

    async def validate_answer_node(state: AgentState) -> dict[str, object]:
        citations = cast(list[dict[str, object]], state.get("citations", []))
        chunks = _as_chunks(state.get("evidence", []))
        valid, grounding_notes = check_grounding(citations, chunks)
        claims_supported, claim_notes = await check_claim_support(state.get("answer", ""), chunks)
        polarity_consistent, polarity_notes = True, []
        if claims_supported:
            polarity_consistent, polarity_notes = await check_yes_no_consistency(
                state.get("query", ""), state.get("answer", ""), chunks
            )
        return {
            "citations": cast(list[CitationItem], valid),
            "answer_valid": bool(
                valid
                and claims_supported
                and polarity_consistent
                and state.get("answer", "").strip()
            ),
            "notes": [
                *state.get("notes", []),
                *grounding_notes,
                *claim_notes,
                *polarity_notes,
            ],
        }

    async def abstain_node(state: AgentState) -> dict[str, object]:
        clarification = state.get("clarification")
        return {
            "answer": clarification
            or (
                "I don't have enough official evidence to answer that reliably. "
                "Please make the course, term, program, or deadline more specific."
            ),
            "citations": [],
            "confidence": 0.2,
            "notes": [*state.get("notes", []), "Strict cite-or-abstain policy applied."],
            "answer_valid": False,
        }

    def after_understand(state: AgentState) -> Literal["retrieve", "abstain"]:
        return "abstain" if state.get("needs_clarification") else "retrieve"

    def after_evidence(
        state: AgentState,
    ) -> Literal["answer", "prepare_retry", "abstain"]:
        if state.get("evidence_valid"):
            return "answer"
        if state.get("retry_count", 0) < 1:
            return "prepare_retry"
        return "abstain"

    def after_answer(state: AgentState) -> Literal["__end__", "abstain"]:
        return END if state.get("answer_valid") else "abstain"

    builder = StateGraph(AgentState)
    builder.add_node("understand", understand_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("validate_evidence", validate_evidence_node)
    builder.add_node("prepare_retry", prepare_retry_node)
    builder.add_node("answer", answer_node)
    builder.add_node("validate_answer", validate_answer_node)
    builder.add_node("abstain", abstain_node)
    builder.add_edge(START, "understand")
    builder.add_conditional_edges("understand", after_understand)
    builder.add_edge("retrieve", "validate_evidence")
    builder.add_conditional_edges("validate_evidence", after_evidence)
    builder.add_edge("prepare_retry", "retrieve")
    builder.add_edge("answer", "validate_answer")
    builder.add_conditional_edges("validate_answer", after_answer)
    builder.add_edge("abstain", END)
    return builder.compile(checkpointer=checkpointer, name="buzzbot-agentic-rag")
