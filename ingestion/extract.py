"""Content extraction using trafilatura with readability-lxml fallback."""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
import trafilatura
from lxml import html as lxml_html
from readability import Document as ReadabilityDoc

logger = structlog.get_logger(__name__)


@dataclass
class ExtractedContent:
    url: str
    title: str | None
    text: str
    table_rows: list[dict] = field(default_factory=list)
    success: bool = True
    method: str = "trafilatura"


def _extract_title_from_html(html: str) -> str | None:
    """Best-effort page title extraction when trafilatura metadata is empty."""
    try:
        root = lxml_html.fromstring(html)
    except Exception:
        return None

    title_candidates = [
        root.xpath("//meta[@property='og:title']/@content"),
        root.xpath("//meta[@name='twitter:title']/@content"),
        root.xpath("//title/text()"),
        root.xpath("//h1//text()"),
    ]
    for values in title_candidates:
        for value in values:
            normalized = " ".join(str(value).split()).strip()
            if normalized:
                return normalized
    return None


def _extract_table_rows(html: str, max_tables: int = 8, max_rows_per_table: int = 80) -> list[dict]:
    rows: list[dict] = []
    try:
        root = lxml_html.fromstring(html)
        tables = root.xpath("//table")
    except Exception:
        return rows

    for t_idx, table in enumerate(tables[:max_tables]):
        title = ""
        caption = table.xpath(".//caption//text()")
        if caption:
            title = " ".join(" ".join(caption).split())

        header_nodes = table.xpath(".//tr[1]/*[self::th or self::td]")
        headers = [" ".join(" ".join(cell.xpath(".//text()")).split()) for cell in header_nodes]
        row_nodes = table.xpath(".//tr")[1:] if headers else table.xpath(".//tr")

        for r_idx, row in enumerate(row_nodes[:max_rows_per_table]):
            cell_nodes = row.xpath("./th|./td")
            cells = [" ".join(" ".join(cell.xpath(".//text()")).split()) for cell in cell_nodes]
            if not any(cells):
                continue

            if headers and len(headers) == len(cells):
                if len(cells) > 1 and (not headers[0] or cell_nodes[0].tag.lower() == "th"):
                    row_text = "\n".join(
                        f"{headers[i]} — {cells[0]}: {cells[i]}"
                        for i in range(1, len(cells))
                        if headers[i] and cells[i]
                    )
                else:
                    row_text = " | ".join(
                        f"{header}: {cell}"
                        for header, cell in zip(headers, cells, strict=True)
                        if header and cell
                    )
            else:
                row_text = " | ".join(cell for cell in cells if cell)

            if not row_text:
                continue

            if title:
                row_text = f"{title} | {row_text}"

            rows.append(
                {
                    "table_index": t_idx,
                    "row_index": r_idx,
                    "text": row_text,
                }
            )
    return rows


def _with_table_rows(text: str, table_rows: list[dict]) -> str:
    structured = "\n".join(str(row.get("text", "")).strip() for row in table_rows).strip()
    return f"{text.strip()}\n\n{structured}".strip() if structured else text.strip()


def extract_content(url: str, html: str) -> ExtractedContent:
    """Extract clean text and title from HTML."""
    table_rows = _extract_table_rows(html)

    # Try trafilatura first
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
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
    if not title:
        title = _extract_title_from_html(html)

    if text and len(text.strip()) > 50:
        return ExtractedContent(
            url=url,
            title=title,
            text=_with_table_rows(text, table_rows),
            table_rows=table_rows,
        )

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
                url=url,
                title=title,
                text=_with_table_rows(fallback_text, table_rows),
                table_rows=table_rows,
                method="readability",
            )
    except Exception as exc:
        logger.warning("readability extraction failed", url=url, error=str(exc))

    return ExtractedContent(
        url=url,
        title=title,
        text="",
        table_rows=table_rows,
        success=False,
        method="none",
    )
