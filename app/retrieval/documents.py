from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.retrieval import (
    _cap_chunks_per_url,
    _lexical_match_score,
    hybrid_retrieve,
    vector_search,
)
from ingestion.documents.registry import load_document_sources

_SOURCES = load_document_sources()
SOURCE_NAMES_BY_TYPE = {
    source_type: tuple(source.name for source in _SOURCES if source.source_type == source_type)
    for source_type in {source.source_type for source in _SOURCES}
}
SOURCE_TYPES_BY_VERTICAL = {
    vertical: tuple(
        dict.fromkeys(source.source_type for source in _SOURCES if source.vertical == vertical)
    )
    for vertical in {source.vertical for source in _SOURCES}
}
OFFICIAL_SOURCE_NAMES = [source.name for source in _SOURCES]


def policy_source_types(query: str) -> tuple[str, ...]:
    lowered = query.lower()
    if "omscs" in lowered:
        return ("omscs_policy",)
    if any(
        cue in lowered
        for cue in (
            "enrollment deferral",
            "deferred first-year",
            "deferred first year",
        )
    ):
        return ("admissions",)
    if re.search(r"\b(?:sat|act)\b", lowered):
        return ("admissions",)
    if "waitlist" in lowered:
        if any(
            cue in lowered
            for cue in (
                "admission",
                "application",
                "first-year",
                "first year",
                "waitlist spot",
                "commit",
            )
        ):
            return ("admissions",)
        return ("official_policy",)
    if "time ticket" in lowered and not any(cue in lowered for cue in ("room", "housing")):
        return ("official_policy",)
    if (
        "summer" in lowered
        and "credit" in lowered
        and any(cue in lowered for cue in ("maximum", "more than", "exceed", "limit"))
    ):
        return ("official_policy",)
    if re.search(r"\baudit(?:ing)?\b", lowered) and any(
        cue in lowered for cue in ("class", "course", "academic credit")
    ):
        return ("academic_policy",)
    vertical_cues = (
        (
            "health_support",
            (
                "immunization",
                "igra",
                "vaccine",
                "disability",
                "accommodation",
                "emotional support animal",
                "temporary injur",
                "documented emergency",
                "student emergency",
                "emergency on-call",
                "mental health",
                "well-being",
                "wellbeing",
                "paratransit",
            ),
        ),
        (
            "housing_dining",
            (
                "housing",
                "room selection",
                "room-selection",
                "meal plan",
                "meal-plan",
                "dining dollar",
                "meal swipe",
                "grubhub",
                "live on campus",
            ),
        ),
        (
            "finance",
            (
                "financial aid",
                "financial-aid",
                "tuition",
                "payment",
                "pay a bill",
                "cost of attendance",
                "loan",
                "webcheck",
                "bursar",
                "fafsa",
                "scholarship",
                "disbursement",
                "graduate plus",
            ),
        ),
        (
            "international",
            (
                "f-1",
                "j-1",
                "istart",
                "international student",
                "cpt",
                "sevis",
                "i-20",
                "ds-2019",
            ),
        ),
        (
            "campus_operations",
            ("stinger", "parking", "transportation", "transit", "shuttle"),
        ),
        (
            "student_life",
            ("knack", "tutoring", "tutor", "student engagement", "academic support"),
        ),
        (
            "admissions",
            (
                "first-year",
                "first year",
                "early action",
                "regular decision",
                "common app",
                "recommendation",
                "transfer applicant",
                "transfer application",
                "transfer document",
                "transfer english proficiency",
                "graduate applicant",
                "graduate application",
                "admission",
            ),
        ),
        (
            "academics",
            (
                "transcript",
                "graduation status",
                "degree",
                "minor",
                "gpa",
                "residency requirement",
                "course load",
                "schedule load",
                "transfer credit",
                "transfer-credit",
                "double-counting",
                "credits shared",
                "bs/ms",
                "master's completion",
                "total credits",
                "academic credit",
            ),
        ),
    )
    for vertical, cues in vertical_cues:
        if any(cue in lowered for cue in cues):
            return SOURCE_TYPES_BY_VERTICAL[vertical]
    return ()


@dataclass(frozen=True)
class PolicyQuery:
    text: str
    source_types: tuple[str, ...] = ()
    top_k: int = 5

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("text is required")
        if any(source_type not in SOURCE_NAMES_BY_TYPE for source_type in self.source_types):
            raise ValueError("unknown source type")
        if not 1 <= self.top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")


@dataclass(frozen=True)
class DocumentEvidence:
    chunk_id: str
    text: str
    title: str | None
    canonical_url: str
    source_name: str
    source_type: str
    authority: str
    fetched_at: str | None
    edition: str | None
    score: float
    retrieval_method: str
    vertical: str | None = None
    content_type: str | None = None
    page: int | None = None


async def search_policy_docs(
    session: AsyncSession,
    query: PolicyQuery,
    query_embedding: list[float],
) -> list[DocumentEvidence]:
    if query.source_types:
        source_filter = [
            name for source_type in query.source_types for name in _source_names(source_type)
        ]
    else:
        source_filter = OFFICIAL_SOURCE_NAMES

    chunks = await hybrid_retrieve(
        session,
        query.text,
        query_embedding,
        top_k=query.top_k,
        source_filter=source_filter,
        force_fts=True,
        max_chunks_per_url=1,
    )
    urls = list(dict.fromkeys(chunk.url for chunk in chunks if chunk.url))
    if urls and query_embedding:
        url_rank = {url: rank for rank, url in enumerate(urls)}
        children = await vector_search(
            session,
            query_embedding,
            top_k=query.top_k * 12,
            source_filter=source_filter,
            url_filter=urls,
            similarity_threshold=-1.0,
        )
        if children:
            children.sort(
                key=lambda chunk: (
                    chunk.score,
                    _lexical_match_score(query.text, chunk),
                    -url_rank.get(chunk.url, len(url_rank)),
                ),
                reverse=True,
            )
            child_ids = {chunk.chunk_id for chunk in children}
            chunks = _cap_chunks_per_url(
                [*children, *(chunk for chunk in chunks if chunk.chunk_id not in child_ids)],
                max_chunks_per_url=2,
                top_k=query.top_k,
            )
            for chunk in chunks:
                chunk.method = "parent_child_vector"
    seen: set[str] = set()
    evidence: list[DocumentEvidence] = []
    for chunk in chunks:
        if chunk.chunk_id in seen or not chunk.url or not chunk.source_name:
            continue
        seen.add(chunk.chunk_id)
        metadata = chunk.metadata_json or {}
        evidence.append(
            DocumentEvidence(
                chunk_id=chunk.chunk_id,
                text=chunk.chunk_text,
                title=chunk.title,
                canonical_url=chunk.url,
                source_name=chunk.source_name,
                source_type=str(metadata.get("source_type", "official_document")),
                authority=str(metadata.get("authority", "official_gt")),
                fetched_at=chunk.fetched_at,
                edition=metadata.get("edition"),
                score=chunk.score,
                retrieval_method=chunk.method,
                vertical=str(metadata["vertical"]) if metadata.get("vertical") else None,
                content_type=(
                    str(metadata["content_type"]) if metadata.get("content_type") else None
                ),
                page=(
                    int(metadata["page_start"])
                    if isinstance(metadata.get("page_start"), int)
                    else None
                ),
            )
        )
    return evidence


def _source_names(source_type: str) -> tuple[str, ...]:
    names = SOURCE_NAMES_BY_TYPE[source_type]
    return (names,) if isinstance(names, str) else names
