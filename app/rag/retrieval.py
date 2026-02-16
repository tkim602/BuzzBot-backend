"""Retrieval — vector similarity (pgvector) + lexical fallback (FTS)."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.config import settings
from app.core.usage import check_limit_or_raise, record_usage
from db.models import Chunk, Embedding, Source

logger = structlog.get_logger(__name__)

COURSE_CODE_RE = re.compile(r"\b([a-z]{2,4})\s*-?\s*(\d{4}[a-z]?)\b", re.IGNORECASE)
TERM_RE_1 = re.compile(r"\b(spring|summer|fall)\s*(20\d{2})\b", re.IGNORECASE)
TERM_RE_2 = re.compile(r"\b(20\d{2})\s*(spring|summer|fall)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9\-]+")
COURSE_CODE_STOPWORDS = {
    "spring", "summer", "fall", "term", "year", "this", "next", "last", "the",
}
FTS_STOPWORDS = {
    "what", "when", "where", "which", "who", "how", "is", "are", "was", "were",
    "a", "an", "the", "for", "to", "of", "in", "on", "at", "with", "and", "or",
    "many", "does", "do", "did", "can", "could", "would", "should", "please",
}

_embedding_cache = TTLCache[list[float]](
    max_size=settings.rag_embedding_cache_max_size,
    ttl_seconds=settings.rag_embedding_cache_ttl_seconds,
)


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


@dataclass
class QueryHints:
    course_code: str | None = None
    term_name: str | None = None
    expanded_query: str = ""


def _extract_query_hints(query: str) -> QueryHints:
    course_code: str | None = None
    term_name: str | None = None

    m = COURSE_CODE_RE.search(query)
    if m:
        dept = m.group(1).lower()
        if dept not in COURSE_CODE_STOPWORDS:
            course_code = f"{m.group(1).upper()} {m.group(2).upper()}"

    m = TERM_RE_1.search(query)
    if m:
        term_name = f"{m.group(1).capitalize()} {m.group(2)}"
    else:
        m = TERM_RE_2.search(query)
        if m:
            term_name = f"{m.group(2).capitalize()} {m.group(1)}"

    expansions: list[str] = [query]
    if course_code:
        expansions.extend(
            [
                course_code,
                course_code.replace(" ", ""),
                course_code.replace(" ", "-"),
            ]
        )
    if term_name:
        expansions.append(term_name)

    # Keep order while de-duplicating
    expanded_query = " ".join(dict.fromkeys(expansions))
    return QueryHints(
        course_code=course_code,
        term_name=term_name,
        expanded_query=expanded_query,
    )


def _embedding_cache_key(provider: str, model: str, query: str) -> str:
    normalized = " ".join(query.strip().lower().split())
    raw = f"{provider}|{model}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _compact_query_for_fts(query: str, max_tokens: int = 12) -> str:
    """Keep high-signal lexical tokens to make FTS cheaper and more precise."""
    tokens = _TOKEN_RE.findall(query)
    if not tokens:
        return query
    # Preserve order and drop very short noise tokens.
    filtered: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        if len(t) < 2:
            continue
        tl = t.lower()
        if tl in FTS_STOPWORDS:
            continue
        if tl in seen:
            continue
        seen.add(tl)
        filtered.append(t)
        if len(filtered) >= max_tokens:
            break
    return " ".join(filtered) if filtered else query


async def get_query_embedding(query: str) -> list[float]:
    """Get embedding for a query string."""
    provider = os.getenv("LLM_PROVIDER", "openai")

    if provider == "openai":
        import openai
        model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        if settings.rag_enable_embedding_cache:
            cache_key = _embedding_cache_key(provider, model, query)
            cached = _embedding_cache.get(cache_key)
            if cached is not None:
                return list(cached)

        # Check usage limit before API call
        check_limit_or_raise()

        client = openai.AsyncOpenAI()
        resp = await client.embeddings.create(input=[query], model=model)

        # Record usage
        total_tokens = resp.usage.total_tokens
        record_usage(model, total_tokens, "embedding")
        embedding = resp.data[0].embedding
        if settings.rag_enable_embedding_cache:
            _embedding_cache.set(cache_key, embedding)
        return embedding

    elif provider == "ollama":
        import httpx

        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model = os.getenv("OLLAMA_MODEL", "llama3")
        if settings.rag_enable_embedding_cache:
            cache_key = _embedding_cache_key(provider, model, query)
            cached = _embedding_cache.get(cache_key)
            if cached is not None:
                return list(cached)
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base}/api/embeddings", json={"model": model, "prompt": query}
            )
            resp.raise_for_status()
            embedding = resp.json()["embedding"]
            if settings.rag_enable_embedding_cache:
                _embedding_cache.set(cache_key, embedding)
            return embedding

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
    metadata_course_code: str | None = None,
    metadata_term_name: str | None = None,
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
            Source.name.label("source_name"),
            Chunk.fetched_at,
            Chunk.headings,
            distance_expr.label("distance"),
        )
        .join(Embedding, Embedding.chunk_id == Chunk.chunk_id)
        .join(Source, Source.id == Chunk.source_id)
    )

    if source_filter:
        stmt = stmt.where(Source.name == source_filter)

    if metadata_course_code:
        stmt = stmt.where(
            func.upper(Chunk.metadata_json["course_code"].astext) == metadata_course_code.upper()
        )
    if metadata_term_name:
        stmt = stmt.where(
            func.lower(Chunk.metadata_json["term_name"].astext) == metadata_term_name.lower()
        )

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
                source_name=row.source_name,
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
    metadata_course_code: str | None = None,
    metadata_term_name: str | None = None,
) -> list[RetrievedChunk]:
    """Full-text search fallback using PostgreSQL tsvector."""
    if not query.strip():
        return []

    optimized_query = _compact_query_for_fts(query)
    ts_vector = func.to_tsvector("simple", Chunk.chunk_text)
    ts_query = func.websearch_to_tsquery("simple", optimized_query)
    rank_expr = func.ts_rank_cd(ts_vector, ts_query)

    stmt = (
        select(
            Chunk.chunk_id,
            Chunk.url,
            Chunk.title,
            Chunk.chunk_text,
            Chunk.fetched_at,
            Chunk.headings,
            Source.name.label("source_name"),
            rank_expr.label("rank"),
        )
        .join(Source, Source.id == Chunk.source_id)
        .where(ts_vector.op("@@")(ts_query))
    )
    if source_filter:
        stmt = stmt.where(Source.name == source_filter)
    if metadata_course_code:
        stmt = stmt.where(
            func.upper(Chunk.metadata_json["course_code"].astext) == metadata_course_code.upper()
        )
    if metadata_term_name:
        stmt = stmt.where(
            func.lower(Chunk.metadata_json["term_name"].astext) == metadata_term_name.lower()
        )

    stmt = stmt.order_by(rank_expr.desc()).limit(top_k)
    result = await session.execute(stmt)
    rows = result.all()

    return [
        RetrievedChunk(
            chunk_id=str(row.chunk_id),
            url=row.url,
            title=row.title,
            chunk_text=row.chunk_text,
            score=float(row.rank),
            source_name=row.source_name,
            fetched_at=row.fetched_at.isoformat() if row.fetched_at else None,
            headings=row.headings,
            method="fts",
        )
        for row in rows
    ]


async def exact_course_code_search(
    session: AsyncSession,
    course_code: str,
    top_k: int = 6,
    source_filter: str | None = None,
) -> list[RetrievedChunk]:
    """Lexical exact-match search for course code patterns like 'CS 2110'."""
    cc = course_code.strip()
    compact = cc.replace(" ", "")
    dashed = cc.replace(" ", "-")
    like_patterns = [f"%{cc}%", f"%{compact}%", f"%{dashed}%"]

    stmt = (
        select(
            Chunk.chunk_id,
            Chunk.url,
            Chunk.title,
            Chunk.chunk_text,
            Chunk.fetched_at,
            Chunk.headings,
            Source.name.label("source_name"),
        )
        .join(Source, Source.id == Chunk.source_id)
        .where(
            func.lower(Chunk.chunk_text).like(like_patterns[0].lower())
            | func.lower(Chunk.chunk_text).like(like_patterns[1].lower())
            | func.lower(Chunk.chunk_text).like(like_patterns[2].lower())
        )
    )
    if source_filter:
        stmt = stmt.where(Source.name == source_filter)
    stmt = stmt.order_by(Chunk.fetched_at.desc().nullslast()).limit(top_k)
    result = await session.execute(stmt)
    rows = result.all()

    return [
        RetrievedChunk(
            chunk_id=str(row.chunk_id),
            url=row.url,
            title=row.title,
            chunk_text=row.chunk_text,
            score=1.0,
            source_name=row.source_name,
            fetched_at=row.fetched_at.isoformat() if row.fetched_at else None,
            headings=row.headings,
            method="exact_code",
        )
        for row in rows
    ]


def _rrf_fuse_results(
    vector_results: list[RetrievedChunk],
    fts_results: list[RetrievedChunk],
    top_k: int,
    k: int = 60,
) -> list[RetrievedChunk]:
    """Fuse vector and lexical rankings with reciprocal rank fusion."""
    if not vector_results and not fts_results:
        return []

    scores: dict[str, float] = {}
    chunks_by_id: dict[str, RetrievedChunk] = {}
    methods_by_id: dict[str, set[str]] = {}

    for rank, chunk in enumerate(vector_results, start=1):
        cid = chunk.chunk_id
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        chunks_by_id.setdefault(cid, chunk)
        methods_by_id.setdefault(cid, set()).add("vector")

    for rank, chunk in enumerate(fts_results, start=1):
        cid = chunk.chunk_id
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        chunks_by_id.setdefault(cid, chunk)
        methods_by_id.setdefault(cid, set()).add("fts")

    ranked_ids = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    merged: list[RetrievedChunk] = []
    for cid, fused_score in ranked_ids:
        chunk = chunks_by_id[cid]
        methods = methods_by_id.get(cid, set())
        if len(methods) > 1:
            chunk.method = "hybrid_rrf"
        chunk.score = fused_score
        merged.append(chunk)

    return merged


def _signal_match_count(chunk: RetrievedChunk, hints: QueryHints) -> int:
    haystack = f"{chunk.title or ''}\n{chunk.chunk_text}".lower()
    score = 0
    if hints.course_code:
        compact = hints.course_code.replace(" ", "").lower()
        if hints.course_code.lower() in haystack or compact in haystack:
            score += 1
    if hints.term_name and hints.term_name.lower() in haystack:
        score += 1
    return score


async def hybrid_retrieve(
    session: AsyncSession,
    query: str,
    query_embedding: list[float],
    top_k: int = 8,
    source_filter: str | None = None,
    similarity_threshold: float = 0.3,
) -> list[RetrievedChunk]:
    """Combined vector + FTS retrieval with reciprocal rank fusion."""
    hints = _extract_query_hints(query)
    metadata_course_code = hints.course_code if source_filter == "gt-scheduler" else None
    metadata_term_name = hints.term_name if source_filter == "gt-scheduler" else None
    keyword_query = hints.expanded_query or query

    fts_top_k = max(3, min(top_k, settings.rag_fts_top_k))

    exact_schedule_lookup = (
        source_filter == "gt-scheduler" and metadata_course_code and metadata_term_name
    )
    if settings.rag_skip_fts_for_exact_schedule and exact_schedule_lookup:
        vector_results = await vector_search(
            session, query_embedding, top_k=top_k,
            source_filter=source_filter, similarity_threshold=similarity_threshold,
            metadata_course_code=metadata_course_code, metadata_term_name=metadata_term_name,
        )
        if len(vector_results) >= min(3, top_k):
            return vector_results[:top_k]
        fts_results = await fts_search(
            session,
            keyword_query,
            top_k=max(fts_top_k, top_k),
            source_filter=source_filter,
            metadata_course_code=metadata_course_code,
            metadata_term_name=metadata_term_name,
        )
    else:
        vector_results = await vector_search(
            session, query_embedding, top_k=top_k,
            source_filter=source_filter, similarity_threshold=similarity_threshold,
            metadata_course_code=metadata_course_code, metadata_term_name=metadata_term_name,
        )
        if (
            settings.rag_skip_fts_when_vector_sufficient
            and len(vector_results) >= top_k
            and not hints.course_code
        ):
            return vector_results[:top_k]
        fts_results = await fts_search(
            session,
            keyword_query,
            top_k=fts_top_k,
            source_filter=source_filter,
            metadata_course_code=metadata_course_code,
            metadata_term_name=metadata_term_name,
        )

    merged = _rrf_fuse_results(vector_results, fts_results, top_k=top_k)
    if hints.course_code:
        exact_results = await exact_course_code_search(
            session,
            hints.course_code,
            top_k=max(3, top_k // 2),
            source_filter=source_filter,
        )
        seen: set[str] = set()
        prioritized: list[RetrievedChunk] = []
        for c in exact_results + merged:
            if c.chunk_id in seen:
                continue
            seen.add(c.chunk_id)
            prioritized.append(c)
        merged = prioritized[:top_k]

    merged.sort(
        key=lambda c: (_signal_match_count(c, hints), c.score),
        reverse=True,
    )
    return merged[:top_k]
