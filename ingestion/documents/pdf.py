from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import pdfplumber


class PdfExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str


@dataclass(frozen=True)
class ExtractedPdf:
    title: str
    pages: tuple[ExtractedPage, ...]


def extract_pdf(
    data: bytes,
    *,
    max_bytes: int = 10 * 1024 * 1024,
    max_pages: int = 200,
) -> ExtractedPdf:
    if not data or len(data) > max_bytes:
        raise PdfExtractionError("PDF size exceeds the safety ceiling")
    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            if len(pdf.pages) > max_pages:
                raise PdfExtractionError("PDF page ceiling exceeded")
            pages = tuple(
                ExtractedPage(index, _normalize_page(page.extract_text() or ""))
                for index, page in enumerate(pdf.pages, 1)
            )
            pages = tuple(page for page in pages if page.text)
            metadata_title = str((pdf.metadata or {}).get("Title") or "").strip()
    except PdfExtractionError:
        raise
    except Exception as exc:
        raise PdfExtractionError("invalid or encrypted PDF") from exc
    if not pages:
        raise PdfExtractionError("PDF has no usable text")
    first_line = next(line.strip() for line in pages[0].text.splitlines() if line.strip())
    return ExtractedPdf((metadata_title or first_line)[:1024], pages)


def _normalize_page(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()
