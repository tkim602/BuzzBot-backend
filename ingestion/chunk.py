"""Token-aware text chunking with overlap."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)
MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")

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


def _looks_like_heading(line: str) -> bool:
    if not line:
        return False
    m = MARKDOWN_HEADING_RE.match(line)
    if m:
        return True
    if len(line) > 100:
        return False
    if line.endswith((".", "!", "?")):
        return False
    if line.lower().startswith(("http://", "https://")):
        return False
    words = line.split()
    if len(words) > 12:
        return False
    alpha_words = [w for w in words if any(ch.isalpha() for ch in w)]
    if not alpha_words:
        return False
    title_like = sum(1 for w in alpha_words if w[:1].isupper()) / max(len(alpha_words), 1)
    return title_like >= 0.6


def _normalize_heading(line: str) -> str:
    m = MARKDOWN_HEADING_RE.match(line)
    if m:
        return m.group(2).strip()
    return line.strip()


def _split_sections(text: str) -> list[tuple[str | None, str, str]]:
    lines = text.splitlines()
    sections: list[tuple[str | None, str, str]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for raw in lines:
        line = raw.strip()
        if _looks_like_heading(line):
            if current_lines:
                body = "\n".join(current_lines).strip()
                if body:
                    path = current_heading or "document"
                    sections.append((current_heading, path, body))
            current_heading = _normalize_heading(line)
            current_lines = [current_heading]
            continue
        current_lines.append(raw)

    if current_lines:
        body = "\n".join(current_lines).strip()
        if body:
            path = current_heading or "document"
            sections.append((current_heading, path, body))

    # If we did not discover useful section boundaries, fall back to token window chunking.
    has_real_heading = any(s[0] for s in sections)
    return sections if has_real_heading else []


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
    sections = _split_sections(text)
    if not sections:
        sections = [(None, "document", text)]

    chunks: list[ChunkResult] = []
    idx = 0
    step = max(1, chunk_size - chunk_overlap)

    for section_heading, section_path, section_text in sections:
        tokens = _encode(section_text)
        total = len(tokens)
        if total <= chunk_size:
            stripped = section_text.strip()
            if _count_tokens(stripped) < min_chunk_size and chunks:
                continue
            chunk_meta = dict(metadata)
            if section_heading:
                chunk_meta["section_heading"] = section_heading
                chunk_meta["page_section_path"] = section_path
            chunk_hash = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
            chunks.append(
                ChunkResult(
                    text=stripped,
                    token_count=_count_tokens(stripped),
                    chunk_hash=chunk_hash,
                    index=idx,
                    metadata=chunk_meta,
                )
            )
            idx += 1
            continue

        start = 0
        while start < total:
            end = min(start + chunk_size, total)
            chunk_tokens = tokens[start:end]
            chunk_text_str = _decode(chunk_tokens).strip()
            if _count_tokens(chunk_text_str) >= min_chunk_size:
                chunk_meta = dict(metadata)
                if section_heading:
                    chunk_meta["section_heading"] = section_heading
                    chunk_meta["page_section_path"] = section_path
                chunk_hash = hashlib.sha256(chunk_text_str.encode("utf-8")).hexdigest()
                chunks.append(
                    ChunkResult(
                        text=chunk_text_str,
                        token_count=_count_tokens(chunk_text_str),
                        chunk_hash=chunk_hash,
                        index=idx,
                        metadata=chunk_meta,
                    )
                )
                idx += 1
            start += step

    return chunks
