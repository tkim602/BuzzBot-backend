from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.retrieval import hybrid_retrieve
from ingestion.documents.registry import load_document_sources

_SOURCES = load_document_sources()
SOURCE_NAMES_BY_TYPE = {
    source_type: tuple(source.name for source in _SOURCES if source.source_type == source_type)
    for source_type in {source.source_type for source in _SOURCES}
}
OFFICIAL_SOURCE_NAMES = [source.name for source in _SOURCES]


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
