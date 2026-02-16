"""Grounding check — verify that citation quotes are supported by retrieved chunks."""

from __future__ import annotations

import structlog

from app.rag.retrieval import RetrievedChunk

logger = structlog.get_logger(__name__)


def check_grounding(
    citations: list[dict],
    chunks: list[RetrievedChunk],
    min_overlap_ratio: float = 0.5,
) -> tuple[list[dict], list[str]]:
    """Verify each citation quote is grounded in a retrieved chunk.

    Returns:
        (valid_citations, notes) — filtered citations + warning notes.
    """
    if not citations:
        return [], []

    # Build text lookup by URL
    chunk_texts: dict[str, list[str]] = {}
    all_text = ""
    for c in chunks:
        key = c.url or "unknown"
        chunk_texts.setdefault(key, []).append(c.chunk_text)
        all_text += " " + c.chunk_text

    valid: list[dict] = []
    notes: list[str] = []

    for cit in citations:
        quote = cit.get("quote", "").strip()
        url = cit.get("url", "")

        if not quote:
            valid.append(cit)
            continue

        # Check if quote is substring of any chunk text
        grounded = False

        # First check URL-specific chunks
        if url in chunk_texts:
            for ct in chunk_texts[url]:
                if _is_grounded(quote, ct, min_overlap_ratio):
                    grounded = True
                    break

        # Fallback: check all chunks
        if not grounded:
            if _is_grounded(quote, all_text, min_overlap_ratio):
                grounded = True

        if grounded:
            valid.append(cit)
        else:
            notes.append(f"Citation quote not fully grounded: '{quote[:80]}...'")
            logger.warning("ungrounded citation", url=url, quote=quote[:80])

    return valid, notes


def _is_grounded(quote: str, text: str, min_ratio: float) -> bool:
    """Check if quote is substantially found in text."""
    quote_lower = quote.lower().strip()
    text_lower = text.lower()

    # Exact substring
    if quote_lower in text_lower:
        return True

    # Word overlap check
    quote_words = set(quote_lower.split())
    text_words = set(text_lower.split())
    if not quote_words:
        return True
    overlap = len(quote_words & text_words) / len(quote_words)
    return overlap >= min_ratio
