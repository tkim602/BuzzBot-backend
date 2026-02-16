"""Content extraction using trafilatura with readability-lxml fallback."""

from __future__ import annotations

from dataclasses import dataclass

import structlog
import trafilatura
from readability import Document as ReadabilityDoc

logger = structlog.get_logger(__name__)


@dataclass
class ExtractedContent:
    url: str
    title: str | None
    text: str
    success: bool = True
    method: str = "trafilatura"


def extract_content(url: str, html: str) -> ExtractedContent:
    """Extract clean text and title from HTML."""
    # Try trafilatura first
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
        url=url,
    )
    title = None
    metadata = trafilatura.extract(
        html,
        include_comments=False,
        output_format="json",
        url=url,
    )
    if metadata:
        import json

        try:
            meta_dict = json.loads(metadata)
            title = meta_dict.get("title")
        except (json.JSONDecodeError, TypeError):
            pass

    if text and len(text.strip()) > 50:
        return ExtractedContent(url=url, title=title, text=text.strip())

    # Fallback to readability-lxml
    logger.info("trafilatura insufficient, trying readability", url=url)
    try:
        doc = ReadabilityDoc(html)
        title = title or doc.short_title()
        summary_html = doc.summary()
        # Strip remaining tags simply
        import re

        fallback_text = re.sub(r"<[^>]+>", " ", summary_html)
        fallback_text = re.sub(r"\s+", " ", fallback_text).strip()
        if len(fallback_text) > 50:
            return ExtractedContent(
                url=url, title=title, text=fallback_text, method="readability"
            )
    except Exception as exc:
        logger.warning("readability extraction failed", url=url, error=str(exc))

    return ExtractedContent(url=url, title=title, text="", success=False, method="none")
