"""Retrieval — vector similarity (pgvector) + lexical fallback (FTS)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import structlog
from pgvector.sqlalchemy import Vector
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Chunk, Embedding, Source

logger = structlog.get_logger(__name__)


@dataclass
class RetrievedChunk:
    chunk_id: str
    url: str | None
    title: str | None
    chunk_text: str
    score: float
    source_name: str | None = None
    fetched_at: str | None = None
    headings: str | None = None
    method: str = "vector"


async def get_query_embedding(query: str) -> list[float]:
    """Get embedding for a query string."""
    provider = os.getenv("LLM_PROVIDER", "openai")

    if provider == "openai":
        import openai

        client = openai.AsyncOpenAI()
        model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        resp = await client.embeddings.create(input=[query], model=model)
        return resp.data[0].embedding

    elif provider == "ollama":
        import httpx

        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model = os.getenv("OLLAMA_MODEL", "llama3")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base}/api/embeddings", json={"model": model, "prompt": query}
            )
            resp.raise_for_status()
            return resp.json()["embedding"]

    else:
        from sentence_transformers import SentenceTransformer

        st_model = SentenceTransformer("all-MiniLM-L6-v2")
        return st_model.encode([query])[0].tolist()


async def vector_search(
    session: AsyncSession,
    query_embedding: list[float],
    top_k: int = 8,
    source_filter: str | None = None,
    similarity_threshold: float = 0.3,
) -> list[RetrievedChunk]:
    """Search chunks using pgvector cosine distance."""
    # Build query
    distance_expr = Embedding.embedding.cosine_distance(query_embedding)

    stmt = (
        select(
            Chunk.chunk_id,
            Chunk.url,
            Chunk.title,
            Chunk.chunk_text,
            Chunk.source_id,
            Chunk.fetched_at,
            Chunk.headings,
            distance_expr.label("distance"),
        )
        .join(Embedding, Embedding.chunk_id == Chunk.chunk_id)
    )

    if source_filter:
        stmt = stmt.join(Source, Source.id == Chunk.source_id).where(Source.name == source_filter)

    stmt = stmt.order_by(distance_expr).limit(top_k)

    result = await session.execute(stmt)
    rows = result.all()

    chunks: list[RetrievedChunk] = []
    for row in rows:
        score = 1.0 - float(row.distance)  # Convert distance to similarity
        if score < similarity_threshold:
            continue
        chunks.append(
            RetrievedChunk(
                chunk_id=str(row.chunk_id),
                url=row.url,
                title=row.title,
                chunk_text=row.chunk_text,
                score=score,
                fetched_at=row.fetched_at.isoformat() if row.fetched_at else None,
                headings=row.headings,
                method="vector",
            )
        )
    return chunks


async def fts_search(
    session: AsyncSession,
    query: str,
    top_k: int = 5,
    source_filter: str | None = None,
) -> list[RetrievedChunk]:
    """Full-text search fallback using PostgreSQL tsvector."""
    # Build a tsquery from the query words
    words = query.strip().split()
    ts_query = " & ".join(w for w in words if len(w) > 2)
    if not ts_query:
        return []

    sql = text("""
        SELECT c.chunk_id, c.url, c.title, c.chunk_text, c.fetched_at, c.headings,
               ts_rank(to_tsvector('english', c.chunk_text), plainto_tsquery('english', :query)) AS rank
        FROM chunks c
        WHERE to_tsvector('english', c.chunk_text) @@ plainto_tsquery('english', :query)
        ORDER BY rank DESC
        LIMIT :limit
    """)

    result = await session.execute(sql, {"query": query, "limit": top_k})
    rows = result.all()

    return [
        RetrievedChunk(
            chunk_id=str(row.chunk_id),
            url=row.url,
            title=row.title,
            chunk_text=row.chunk_text,
            score=float(row.rank),
            fetched_at=row.fetched_at.isoformat() if row.fetched_at else None,
            headings=row.headings,
            method="fts",
        )
        for row in rows
    ]


async def hybrid_retrieve(
    session: AsyncSession,
    query: str,
    query_embedding: list[float],
    top_k: int = 8,
    source_filter: str | None = None,
    similarity_threshold: float = 0.3,
) -> list[RetrievedChunk]:
    """Combined vector + FTS retrieval with de-duplication."""
    vector_results = await vector_search(
        session, query_embedding, top_k=top_k,
        source_filter=source_filter, similarity_threshold=similarity_threshold,
    )
    fts_results = await fts_search(session, query, top_k=top_k // 2, source_filter=source_filter)

    # Merge and de-duplicate, preferring vector results
    seen: set[str] = set()
    merged: list[RetrievedChunk] = []
    for chunk in vector_results:
        if chunk.chunk_id not in seen:
            seen.add(chunk.chunk_id)
            merged.append(chunk)
    for chunk in fts_results:
        if chunk.chunk_id not in seen:
            seen.add(chunk.chunk_id)
            merged.append(chunk)

    return merged[:top_k]
