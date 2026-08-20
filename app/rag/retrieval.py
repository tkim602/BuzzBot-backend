"""Retrieval — vector similarity (pgvector) + lexical fallback (FTS)."""

from __future__ import annotations

import hashlib
import math
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
SourceFilter = str | list[str] | None

COURSE_CODE_RE = re.compile(r"\b([a-z]{2,4})\s*-?\s*(\d{4}[a-z]?)\b", re.IGNORECASE)
TERM_RE_1 = re.compile(r"\b(spring|summer|fall)\s*(20\d{2})\b", re.IGNORECASE)
TERM_RE_2 = re.compile(r"\b(20\d{2})\s*(spring|summer|fall)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9\-]+")
COURSE_CODE_STOPWORDS = {
    "spring",
    "summer",
    "fall",
    "term",
    "year",
    "this",
    "next",
    "last",
    "the",
}
AVAILABILITY_TERMS = ("offer", "offered", "offering", "available", "availability", "개설")
FTS_STOPWORDS = {
    "what",
    "when",
    "where",
    "which",
    "who",
    "how",
    "is",
    "are",
    "was",
    "were",
    "a",
    "an",
    "the",
    "for",
    "to",
    "of",
    "in",
    "on",
    "at",
    "with",
    "and",
    "or",
    "many",
    "does",
    "do",
    "did",
    "can",
    "could",
    "would",
    "should",
    "please",
}

_embedding_cache = TTLCache[list[float]](
    max_size=settings.rag_embedding_cache_max_size,
    ttl_seconds=settings.rag_embedding_cache_ttl_seconds,
)
_cross_encoder_model = None
_cross_encoder_model_name: str | None = None


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
    metadata_json: dict | None = None
    method: str = "vector"


@dataclass
class QueryHints:
    course_code: str | None = None
    term_name: str | None = None
    expanded_query: str = ""
    asks_availability: bool = False


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
    asks_availability = any(term in query.lower() for term in AVAILABILITY_TERMS)
    return QueryHints(
        course_code=course_code,
        term_name=term_name,
        expanded_query=expanded_query,
        asks_availability=asks_availability,
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


def _apply_source_filter(stmt, source_filter: SourceFilter):
    if not source_filter:
        return stmt
    if isinstance(source_filter, list):
        if not source_filter:
            return stmt
        return stmt.where(Source.name.in_(source_filter))
    return stmt.where(Source.name == source_filter)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def get_query_embedding(query: str) -> list[float]:
    """Get embedding for a query string."""
    embeddings = await get_text_embeddings([query])
    return embeddings[0] if embeddings else []


async def get_text_embeddings(texts: list[str]) -> list[list[float]]:
    """Get embeddings for text list with provider-aware batching and cache."""
    if not texts:
        return []

    provider = os.getenv("LLM_PROVIDER", settings.llm_provider)
    model = os.getenv("OPENAI_EMBED_MODEL", settings.openai_embed_model)

    uncached_positions: list[int] = []
    resolved: list[list[float] | None] = [None] * len(texts)
    if settings.rag_enable_embedding_cache:
        for idx, text in enumerate(texts):
            key = _embedding_cache_key(provider, model, text)
            cached = _embedding_cache.get(key)
            if cached is None:
                uncached_positions.append(idx)
            else:
                resolved[idx] = list(cached)
    else:
        uncached_positions = list(range(len(texts)))

    if uncached_positions:
        inputs = [texts[i] for i in uncached_positions]
        fresh_embeddings: list[list[float]]

        if provider == "openai":
            import openai

            check_limit_or_raise()
            client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
            resp = await client.embeddings.create(input=inputs, model=model)
            total_tokens = resp.usage.total_tokens if resp.usage else 0
            if total_tokens:
                record_usage(model, total_tokens, "embedding")
            fresh_embeddings = [item.embedding for item in resp.data]
        elif provider == "ollama":
            import httpx

            base = os.getenv("OLLAMA_BASE_URL", settings.ollama_base_url)
            ollama_model = os.getenv("OLLAMA_MODEL", settings.ollama_model)
            fresh_embeddings = []
            async with httpx.AsyncClient(timeout=60) as client:
                for text in inputs:
                    resp = await client.post(
                        f"{base}/api/embeddings",
                        json={"model": ollama_model, "prompt": text},
                    )
                    resp.raise_for_status()
                    fresh_embeddings.append(resp.json()["embedding"])
        else:
            from sentence_transformers import SentenceTransformer

            st_model = SentenceTransformer("all-MiniLM-L6-v2")
            fresh_embeddings = st_model.encode(inputs).tolist()

        for pos, emb in zip(uncached_positions, fresh_embeddings, strict=True):
            resolved[pos] = emb
            if settings.rag_enable_embedding_cache:
                cache_key = _embedding_cache_key(provider, model, texts[pos])
                _embedding_cache.set(cache_key, emb)

    return [emb if emb is not None else [] for emb in resolved]


def rerank_with_cross_encoder(
    query: str,
    chunks: list[RetrievedChunk],
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    """Rerank chunks using a cross-encoder model for more accurate relevance scoring."""
    global _cross_encoder_model, _cross_encoder_model_name
    if not chunks:
        return chunks
    try:
        from sentence_transformers import CrossEncoder

        if _cross_encoder_model is None or _cross_encoder_model_name != settings.rag_rerank_model:
            _cross_encoder_model = CrossEncoder(settings.rag_rerank_model)
            _cross_encoder_model_name = settings.rag_rerank_model
            logger.info("cross-encoder loaded", model=settings.rag_rerank_model)

        model = _cross_encoder_model
        pairs = [[query, _ranking_text(c)] for c in chunks]
        scores = model.predict(pairs)
        for chunk, score in zip(chunks, scores, strict=True):
            chunk.score = float(score)
        chunks.sort(key=lambda c: c.score, reverse=True)
        if top_k is not None:
            chunks = chunks[:top_k]
    except Exception as exc:
        logger.warning("cross-encoder rerank skipped", error=str(exc))
    return chunks


async def rerank_chunks_by_embedding(
    query: str,
    chunks: list[RetrievedChunk],
    alpha: float = 0.75,
) -> list[RetrievedChunk]:
    """Rerank chunks by blending existing score with embedding cosine similarity."""
    if not chunks:
        return chunks
    try:
        query_emb = await get_query_embedding(query)
        chunk_embs = await get_text_embeddings([c.chunk_text for c in chunks])
        for chunk, emb in zip(chunks, chunk_embs, strict=True):
            sim = _cosine_similarity(query_emb, emb)
            chunk.score = (alpha * sim) + ((1.0 - alpha) * chunk.score)
        chunks.sort(key=lambda c: c.score, reverse=True)
    except Exception as exc:
        logger.warning("embedding rerank skipped", error=str(exc))
    return chunks


async def vector_search(
    session: AsyncSession,
    query_embedding: list[float],
    top_k: int = 8,
    source_filter: SourceFilter = None,
    similarity_threshold: float = 0.3,
    metadata_course_code: str | None = None,
    metadata_term_name: str | None = None,
    metadata_semester: str | None = None,
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
            Chunk.metadata_json,
            distance_expr.label("distance"),
        )
        .join(Embedding, Embedding.chunk_id == Chunk.chunk_id)
        .join(Source, Source.id == Chunk.source_id)
    )

    stmt = _apply_source_filter(stmt, source_filter)

    if metadata_course_code:
        stmt = stmt.where(
            func.upper(Chunk.metadata_json["course_code"].astext) == metadata_course_code.upper()
        )
    if metadata_term_name:
        stmt = stmt.where(
            func.lower(Chunk.metadata_json["term_name"].astext) == metadata_term_name.lower()
        )
    if metadata_semester:
        stmt = stmt.where(
            (Source.name != "gt-calendar-events")
            | (func.lower(Chunk.metadata_json["semester"].astext) == metadata_semester.lower())
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
                metadata_json=row.metadata_json,
                method="vector",
            )
        )
    return chunks


async def fts_search(
    session: AsyncSession,
    query: str,
    top_k: int = 5,
    source_filter: SourceFilter = None,
    metadata_course_code: str | None = None,
    metadata_term_name: str | None = None,
    metadata_semester: str | None = None,
    match_any: bool = False,
) -> list[RetrievedChunk]:
    """Full-text search fallback using PostgreSQL tsvector."""
    if not query.strip():
        return []

    optimized_query = _compact_query_for_fts(query)
    if match_any:
        optimized_query = " OR ".join(_TOKEN_RE.findall(optimized_query))
    search_text = func.concat_ws(" ", Chunk.title, Chunk.headings, Chunk.chunk_text)
    ts_vector = func.to_tsvector("simple", search_text)
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
            Chunk.metadata_json,
            Source.name.label("source_name"),
            rank_expr.label("rank"),
        )
        .join(Source, Source.id == Chunk.source_id)
        .where(ts_vector.op("@@")(ts_query))
    )
    stmt = _apply_source_filter(stmt, source_filter)
    if metadata_course_code:
        stmt = stmt.where(
            func.upper(Chunk.metadata_json["course_code"].astext) == metadata_course_code.upper()
        )
    if metadata_term_name:
        stmt = stmt.where(
            func.lower(Chunk.metadata_json["term_name"].astext) == metadata_term_name.lower()
        )
    if metadata_semester:
        stmt = stmt.where(
            (Source.name != "gt-calendar-events")
            | (func.lower(Chunk.metadata_json["semester"].astext) == metadata_semester.lower())
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
            metadata_json=row.metadata_json,
            method="fts",
        )
        for row in rows
    ]


async def exact_course_code_search(
    session: AsyncSession,
    course_code: str,
    top_k: int = 6,
    source_filter: SourceFilter = None,
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
            Chunk.metadata_json,
            Source.name.label("source_name"),
        )
        .join(Source, Source.id == Chunk.source_id)
        .where(
            func.lower(Chunk.chunk_text).like(like_patterns[0].lower())
            | func.lower(Chunk.chunk_text).like(like_patterns[1].lower())
            | func.lower(Chunk.chunk_text).like(like_patterns[2].lower())
        )
    )
    stmt = _apply_source_filter(stmt, source_filter)
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
            metadata_json=row.metadata_json,
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
    if hints.asks_availability and chunk.metadata_json:
        chunk_type = str(chunk.metadata_json.get("type", "")).lower()
        if chunk_type == "course_summary":
            score += 1
    return score


def _ranking_text(chunk: RetrievedChunk) -> str:
    return "\n".join(
        value.strip() for value in (chunk.title, chunk.headings, chunk.chunk_text) if value
    )


def _lexical_match_score(query: str, chunk: RetrievedChunk) -> float:
    query_tokens = {
        token.lower()
        for token in _TOKEN_RE.findall(query)
        if len(token) > 1 and token.lower() not in FTS_STOPWORDS
    }
    if not query_tokens:
        return 0.0
    body_tokens = {token.lower() for token in _TOKEN_RE.findall(chunk.chunk_text)}
    context_tokens = {
        token.lower() for token in _TOKEN_RE.findall(f"{chunk.title or ''} {chunk.headings or ''}")
    }
    weighted_matches = len(query_tokens & body_tokens) + 2 * len(query_tokens & context_tokens)
    return weighted_matches / len(query_tokens)


async def hybrid_retrieve(
    session: AsyncSession,
    query: str,
    query_embedding: list[float],
    top_k: int = 8,
    source_filter: SourceFilter = None,
    similarity_threshold: float = 0.3,
    force_fts: bool = False,
    hyde_embedding: list[float] | None = None,
) -> list[RetrievedChunk]:
    """Combined vector + FTS retrieval with reciprocal rank fusion."""
    # Use HyDE embedding for vector search if provided
    vector_embedding = hyde_embedding if hyde_embedding else query_embedding
    hints = _extract_query_hints(query)
    schedule_only = source_filter == "gt-scheduler" or (
        isinstance(source_filter, list) and source_filter == ["gt-scheduler"]
    )
    includes_calendar_events = source_filter == "gt-calendar-events" or (
        isinstance(source_filter, list) and "gt-calendar-events" in source_filter
    )
    metadata_course_code = hints.course_code if schedule_only else None
    metadata_term_name = hints.term_name if schedule_only else None
    metadata_semester = hints.term_name if includes_calendar_events else None
    keyword_query = hints.expanded_query or query

    fts_top_k = max(3, min(top_k, settings.rag_fts_top_k))

    exact_schedule_lookup = schedule_only and metadata_course_code and metadata_term_name
    if settings.rag_skip_fts_for_exact_schedule and exact_schedule_lookup:
        vector_results = await vector_search(
            session,
            vector_embedding,
            top_k=top_k,
            source_filter=source_filter,
            similarity_threshold=similarity_threshold,
            metadata_course_code=metadata_course_code,
            metadata_term_name=metadata_term_name,
            metadata_semester=metadata_semester,
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
            metadata_semester=metadata_semester,
            match_any=force_fts,
        )
    else:
        vector_results = await vector_search(
            session,
            vector_embedding,
            top_k=top_k,
            source_filter=source_filter,
            similarity_threshold=similarity_threshold,
            metadata_course_code=metadata_course_code,
            metadata_term_name=metadata_term_name,
            metadata_semester=metadata_semester,
        )
        if (
            settings.rag_skip_fts_when_vector_sufficient
            and not force_fts
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
            metadata_semester=metadata_semester,
            match_any=force_fts,
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
        key=lambda c: (_signal_match_count(c, hints), _lexical_match_score(query, c), c.score),
        reverse=True,
    )

    # Cross-encoder reranking for more accurate relevance scoring
    if settings.rag_enable_reranking and len(merged) > 1:
        # Rerank up to 15 candidates, return best top_k
        candidates = merged[:15]
        merged = rerank_with_cross_encoder(query, candidates, top_k=top_k)

    return merged[:top_k]
