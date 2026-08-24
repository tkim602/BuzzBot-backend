"""Embedding and indexing — upsert chunks + embeddings to pgvector."""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, Embedding, FetchState, Source

# Add app to path for usage tracking
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import settings
from app.core.usage import check_limit_or_raise, record_usage

logger = structlog.get_logger(__name__)


def get_embedding_function():
    """Return an embedding function based on configured provider."""
    provider = os.getenv("LLM_PROVIDER", "openai")

    if provider == "openai":
        import openai

        client = openai.OpenAI(api_key=settings.openai_api_key)
        model = os.getenv("OPENAI_EMBED_MODEL", settings.openai_embed_model)

        def embed_texts(texts: list[str]) -> list[list[float]]:
            # Check usage limit before API call
            check_limit_or_raise()

            resp = client.embeddings.create(input=texts, model=model)

            # Record usage
            total_tokens = resp.usage.total_tokens
            record_usage(model, total_tokens, "embedding")

            return [item.embedding for item in resp.data]

        return embed_texts

    elif provider == "ollama":
        import httpx

        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model = os.getenv("OLLAMA_MODEL", "llama3")

        def embed_texts(texts: list[str]) -> list[list[float]]:
            embeddings = []
            for t in texts:
                resp = httpx.post(
                    f"{base}/api/embeddings", json={"model": model, "prompt": t}, timeout=60
                )
                resp.raise_for_status()
                embeddings.append(resp.json()["embedding"])
            return embeddings

        return embed_texts

    else:
        # Fallback: sentence-transformers (CPU)
        from sentence_transformers import SentenceTransformer

        st_model = SentenceTransformer("all-MiniLM-L6-v2")

        def embed_texts(texts: list[str]) -> list[list[float]]:
            return st_model.encode(texts).tolist()

        return embed_texts


def upsert_source(
    session: Session, name: str, base_url: str, allowed: bool, reason: str
) -> uuid.UUID:
    """Upsert a source and return its ID."""
    existing = session.execute(select(Source).where(Source.name == name)).scalar_one_or_none()
    if existing:
        existing.base_url = base_url
        existing.allowed = allowed
        existing.reason = reason
        session.flush()
        return existing.id
    source = Source(name=name, base_url=base_url, allowed=allowed, reason=reason)
    session.add(source)
    session.flush()
    return source.id


def upsert_document(
    session: Session,
    source_id: uuid.UUID,
    url: str,
    title: str | None,
    content_text: str,
    content_hash: str,
    etag: str | None = None,
    last_modified: str | None = None,
) -> uuid.UUID:
    """Upsert a document; returns doc_id. Skips update if content_hash unchanged."""
    existing = session.execute(
        select(Document).where(Document.canonical_url == url)
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if existing:
        if existing.content_hash == content_hash:
            logger.debug("document unchanged", url=url)
            return existing.doc_id
        existing.title = title
        existing.content_text = content_text
        existing.content_hash = content_hash
        existing.fetched_at = now
        existing.etag = etag
        existing.last_modified = last_modified
        session.flush()
        return existing.doc_id

    doc = Document(
        source_id=source_id,
        canonical_url=url,
        title=title,
        content_text=content_text,
        content_hash=content_hash,
        fetched_at=now,
        etag=etag,
        last_modified=last_modified,
    )
    session.add(doc)
    session.flush()
    return doc.doc_id


def index_chunks(
    session: Session,
    doc_id: uuid.UUID,
    source_id: uuid.UUID,
    chunks: list,
    url: str,
    title: str | None,
    headings: str | None,
    fetched_at: datetime | None,
    embed_fn,
    batch_size: int = 32,
) -> int:
    """Index chunks and their embeddings. Returns count of new/updated embeddings."""
    # Delete old chunks for this document
    old_chunks = session.execute(select(Chunk).where(Chunk.doc_id == doc_id)).scalars().all()
    for old in old_chunks:
        session.delete(old)
    session.flush()

    if not chunks:
        return 0

    # Create new chunks
    chunk_objs: list[Chunk] = []
    for c in chunks:
        chunk_obj = Chunk(
            doc_id=doc_id,
            source_id=source_id,
            url=url,
            title=title,
            headings=headings,
            chunk_text=c.text,
            chunk_hash=c.chunk_hash,
            token_count=c.token_count,
            fetched_at=fetched_at,
            metadata_json=c.metadata,
        )
        session.add(chunk_obj)
        chunk_objs.append(chunk_obj)
    session.flush()

    # Embed in batches
    count = 0
    for i in range(0, len(chunk_objs), batch_size):
        batch = chunk_objs[i : i + batch_size]
        texts = [c.chunk_text for c in batch]
        embeddings = embed_fn(texts)
        for chunk_obj, emb in zip(batch, embeddings, strict=True):
            emb_obj = Embedding(chunk_id=chunk_obj.chunk_id, embedding=emb)
            session.add(emb_obj)
            count += 1
    session.flush()

    return count


def update_fetch_state(
    session: Session,
    url: str,
    source_id: uuid.UUID,
    etag: str | None,
    last_modified: str | None,
    content_hash: str | None,
    status: str = "success",
    error: str | None = None,
) -> None:
    """Upsert fetch state for a URL."""
    existing = session.execute(select(FetchState).where(FetchState.url == url)).scalar_one_or_none()
    now = datetime.now(UTC)
    if existing:
        existing.etag = etag
        existing.last_modified = last_modified
        existing.content_hash = content_hash
        existing.last_fetched_at = now
        existing.status = status
        existing.error = error
    else:
        fs = FetchState(
            url=url,
            source_id=source_id,
            etag=etag,
            last_modified=last_modified,
            content_hash=content_hash,
            last_fetched_at=now,
            status=status,
            error=error,
        )
        session.add(fs)
    session.flush()
