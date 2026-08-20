from __future__ import annotations

from contextlib import nullcontext
from unittest.mock import MagicMock

import httpx
import pytest

from ingestion.documents.pdf import PdfExtractionError, extract_pdf
from ingestion.documents.registry import DocumentSource
from ingestion.documents.sync import (
    DocumentSyncOutcome,
    FetchedDocument,
    _chunks_for_fetched,
    sync_document_url,
)


def _pdf_bytes(*pages: str) -> bytes:
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{index + 3} 0 R' for index in range(len(pages)))}] /Count {len(pages)} >>".encode(),
    ]
    content_start = 3 + len(pages)
    font_id = content_start + len(pages)
    for index in range(len(pages)):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_start + index} 0 R >>".encode()
        )
    for text in pages:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines = escaped.split("\n")
        commands = ["BT /F1 12 Tf 72 720 Td"]
        for index, line in enumerate(lines):
            if index:
                commands.append("0 -18 Td")
            commands.append(f"({line}) Tj")
        commands.append("ET")
        stream = "\n".join(commands).encode()
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(result)


def _source() -> DocumentSource:
    return DocumentSource(
        "gt-guide",
        "student_support",
        "official",
        ("https://guide.gatech.edu/",),
        ("https://guide.gatech.edu/guides",),
        5,
        vertical="student_life",
        adapter="paths",
        allowed_path_prefixes=("/guides",),
        content_types=("text/html", "application/pdf"),
    )


def test_pdf_extraction_preserves_page_numbers_and_uses_first_line_title():
    extracted = extract_pdf(
        _pdf_bytes(
            "Run 3 Student Guide\n" + "First page policy text " * 20,
            "Second Page\n" + "Second page deadline text " * 20,
        )
    )

    assert extracted.title == "Run 3 Student Guide"
    assert [page.page_number for page in extracted.pages] == [1, 2]
    assert "First page policy text" in extracted.pages[0].text
    assert "Second page deadline text" in extracted.pages[1].text


def test_pdf_extraction_fails_closed_for_size_page_and_text_quality():
    with pytest.raises(PdfExtractionError, match="size"):
        extract_pdf(_pdf_bytes("text"), max_bytes=10)
    with pytest.raises(PdfExtractionError, match="page ceiling"):
        extract_pdf(_pdf_bytes("one", "two"), max_pages=1)
    with pytest.raises(PdfExtractionError, match="usable text"):
        extract_pdf(_pdf_bytes(""))
    with pytest.raises(PdfExtractionError, match="invalid"):
        extract_pdf(b"not a pdf")


def test_pdf_pages_are_chunked_independently_with_page_metadata():
    extracted = extract_pdf(
        _pdf_bytes(
            "Page One\n" + "housing contract policy " * 30,
            "Page Two\n" + "housing cancellation deadline " * 30,
        )
    )
    fetched = FetchedDocument(
        source_url="https://guide.gatech.edu/guides/student.pdf",
        canonical_url="https://guide.gatech.edu/guides/student.pdf",
        title=extracted.title,
        text="\n\n".join(page.text for page in extracted.pages),
        fetched_at=MagicMock(),
        etag=None,
        last_modified=None,
        edition=None,
        content_type="application/pdf",
        pages=extracted.pages,
    )

    chunks = _chunks_for_fetched(fetched, {"source": "gt-guide"}, min_chunk_size=10)

    assert {chunk.metadata["page_start"] for chunk in chunks} == {1, 2}
    assert all(chunk.metadata["page_start"] == chunk.metadata["page_end"] for chunk in chunks)
    assert all(chunk.metadata["content_type"] == "application/pdf" for chunk in chunks)
    assert not any(
        "Page Two" in chunk.text for chunk in chunks if chunk.metadata["page_start"] == 1
    )


@pytest.mark.asyncio
async def test_pdf_content_type_uses_pdf_extractor_and_shared_store(monkeypatch):
    data = _pdf_bytes("Official Guide\n" + "student policy text " * 30)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=data,
            headers={"Content-Type": "application/pdf"},
            request=request,
        )

    def store(session, source, fetched, embed_fn):
        captured["fetched"] = fetched
        return True, 1

    monkeypatch.setattr("ingestion.documents.sync._store_document", store)
    session = MagicMock()
    session.scalar.return_value = None
    result = await sync_document_url(
        _source(),
        "https://guide.gatech.edu/guides/student.pdf",
        lambda: nullcontext(session),
        lambda texts: [[0.0] * 1536 for _ in texts],
        httpx.MockTransport(handler),
    )

    assert result.outcome is DocumentSyncOutcome.INDEXED
    fetched = captured["fetched"]
    assert isinstance(fetched, FetchedDocument)
    assert fetched.content_type == "application/pdf"
    assert fetched.pages[0].page_number == 1


@pytest.mark.asyncio
async def test_undeclared_response_content_type_is_rejected_before_store(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"archive",
            headers={"Content-Type": "application/zip"},
            request=request,
        )

    monkeypatch.setattr(
        "ingestion.documents.sync._store_document",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not store")),
    )
    session = MagicMock()
    session.scalar.return_value = None

    result = await sync_document_url(
        _source(),
        "https://guide.gatech.edu/guides/archive",
        lambda: nullcontext(session),
        lambda texts: [],
        httpx.MockTransport(handler),
    )

    assert result.outcome is DocumentSyncOutcome.EXTRACT_FAILED
    assert result.reason == "UNEXPECTED_CONTENT_TYPE"


@pytest.mark.asyncio
async def test_broken_pdf_fails_before_replacing_trusted_document(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"broken pdf",
            headers={"Content-Type": "application/pdf"},
            request=request,
        )

    monkeypatch.setattr(
        "ingestion.documents.sync._store_document",
        lambda *args: (_ for _ in ()).throw(AssertionError("trusted data must not be touched")),
    )
    session = MagicMock()
    session.scalar.return_value = None

    result = await sync_document_url(
        _source(),
        "https://guide.gatech.edu/guides/broken.pdf",
        lambda: nullcontext(session),
        lambda texts: [],
        httpx.MockTransport(handler),
    )

    assert result.outcome is DocumentSyncOutcome.EXTRACT_FAILED
    assert result.reason == "QUALITY_GATE_FAILED"
