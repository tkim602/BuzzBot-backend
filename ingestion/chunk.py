"""Token-aware text chunking with overlap."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)

# Try tiktoken, fallback to word-based estimation
try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")

    def _count_tokens(text: str) -> int:
        return len(_enc.encode(text))

    def _encode(text: str) -> list[int]:
        return _enc.encode(text)

    def _decode(tokens: list[int]) -> str:
        return _enc.decode(tokens)

    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False

    def _count_tokens(text: str) -> int:
        return len(text.split())

    def _encode(text: str) -> list[str]:
        return text.split()

    def _decode(tokens: list[str]) -> str:
        return " ".join(tokens)


@dataclass
class ChunkResult:
    text: str
    token_count: int
    chunk_hash: str
    index: int
    metadata: dict = field(default_factory=dict)


def chunk_text(
    text: str,
    chunk_size: int = 500,
    chunk_overlap: int = 80,
    min_chunk_size: int = 50,
    metadata: dict | None = None,
) -> list[ChunkResult]:
    """Split text into token-aware overlapping chunks.

    Args:
        text: Input text to chunk.
        chunk_size: Target tokens per chunk (300-800 range).
        chunk_overlap: Overlap tokens between consecutive chunks.
        min_chunk_size: Minimum tokens for a chunk to be kept.
        metadata: Metadata to attach to each chunk.
    """
    metadata = metadata or {}
    tokens = _encode(text)
    total = len(tokens)

    if total <= chunk_size:
        chunk_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return [
            ChunkResult(
                text=text.strip(),
                token_count=_count_tokens(text),
                chunk_hash=chunk_hash,
                index=0,
                metadata=metadata,
            )
        ]

    chunks: list[ChunkResult] = []
    start = 0
    idx = 0
    step = chunk_size - chunk_overlap

    while start < total:
        end = min(start + chunk_size, total)
        chunk_tokens = tokens[start:end]
        chunk_text_str = _decode(chunk_tokens).strip()

        if _count_tokens(chunk_text_str) >= min_chunk_size:
            chunk_hash = hashlib.sha256(chunk_text_str.encode("utf-8")).hexdigest()
            chunks.append(
                ChunkResult(
                    text=chunk_text_str,
                    token_count=_count_tokens(chunk_text_str),
                    chunk_hash=chunk_hash,
                    index=idx,
                    metadata=metadata,
                )
            )
            idx += 1

        start += step

    return chunks
