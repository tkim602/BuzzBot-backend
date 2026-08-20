from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.retrieval import hybrid_retrieve
from ingestion.documents.registry import load_document_sources

_SOURCES = load_document_sources()
SOURCE_NAMES_BY_TYPE = {source.source_type: source.name for source in _SOURCES}
OFFICIAL_SOURCE_NAMES = [source.name for source in _SOURCES]
DEADLINE_RE = re.compile(r"\b(exact|deadline|last day|academic calendar|what date|when)\b", re.I)


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


async def search_policy_docs(
    session: AsyncSession,
    query: PolicyQuery,
    query_embedding: list[float],
) -> list[DocumentEvidence]:
    if query.source_types:
        source_filter = [SOURCE_NAMES_BY_TYPE[source_type] for source_type in query.source_types]
    elif DEADLINE_RE.search(query.text):
        source_filter = [SOURCE_NAMES_BY_TYPE["academic_calendar"]]
    else:
        source_filter = OFFICIAL_SOURCE_NAMES

    chunks = await hybrid_retrieve(
        session,
        query.text,
        query_embedding,
        top_k=query.top_k,
        source_filter=source_filter,
        force_fts=True,
    )
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
            )
        )
    return evidence
