from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from ingestion.documents.discovery import _declared_path_allowed
from ingestion.documents.pdf import ExtractedPage, ExtractedPdf
from ingestion.documents.registry import DocumentSource, load_document_sources
from ingestion.documents.sync import DocumentSyncOutcome, FetchedDocument, sync_document_url


def test_run3_registry_refreshes_known_stale_and_moved_paths():
    sources = {
        source.name: source
        for source in load_document_sources(Path("ingestion/sources.yaml"))
    }

    programs = sources["gt-catalog-programs"]
    assert "/academics/research-support-facilities/oak-ridge-associated-universities" in (
        programs.excluded_paths
    )
    assert "/programs/management-ms-executive" in programs.excluded_paths

    lifecycle = sources["gt-registrar-lifecycle"]
    assert "https://registrar.gatech.edu/current-students/transcripts" in lifecycle.seed_urls
    assert "https://registrar.gatech.edu/current-students/degree-information" in (
        lifecycle.seed_urls
    )
    assert _declared_path_allowed(lifecycle, "/current-students/transcripts")
    assert _declared_path_allowed(lifecycle, "/current-students/degree-information")

    financial_aid = sources["gt-financial-aid"]
    assert "/manage-aid/grade-substitution" in financial_aid.excluded_paths

    international = sources["gt-international"]
    assert "/content/isss-welcome" in international.excluded_paths
    assert "/sites/default/files/i-94_instructions_updated.pdf" in international.excluded_paths
    assert _declared_path_allowed(international, "/i-20-and-ds-2019-form-guides")

    academic_support = sources["gt-academic-support"]
    assert _declared_path_allowed(academic_support, "/pre-graduate-advising")
    assert _declared_path_allowed(academic_support, "/pre-health")
    assert _declared_path_allowed(academic_support, "/pre-teaching")


def test_health_registry_allows_only_declared_public_cdn_redirect_root():
    sources = {
        source.name: source
        for source in load_document_sources(Path("ingestion/sources.yaml"))
    }
    health = sources["gt-health"]

    assert health.allowed_redirect_roots == ("https://c14750.wpmucdn.com/2790/files/",)
    assert health.allows_redirect(
        "https://c14750.wpmucdn.com/2790/files/2025/05/Immunization-Requirements.pdf"
    )
    assert not health.allows_redirect("https://c14750.wpmucdn.com/other/files/form.pdf")
    assert not health.allows_redirect("https://example.com/2790/files/form.pdf")


def test_document_source_rejects_non_https_redirect_root():
    with pytest.raises(ValueError, match="allowed redirect roots must use HTTPS"):
        DocumentSource(
            name="bad",
            source_type="policy",
            authority="official",
            allowed_roots=("https://example.gatech.edu/",),
            seed_urls=("https://example.gatech.edu/policy",),
            max_urls=1,
            allowed_redirect_roots=("http://cdn.example.com/files/",),
        )


@pytest.mark.asyncio
async def test_health_pdf_follows_only_declared_cdn_and_keeps_official_citation(monkeypatch):
    official_url = "https://health.gatech.edu/files/2025/05/Immunization-Requirements.pdf"
    cdn_url = "https://c14750.wpmucdn.com/2790/files/2025/05/Immunization-Requirements.pdf"
    source = DocumentSource(
        name="gt-health",
        source_type="health_support",
        authority="health",
        allowed_roots=("https://health.gatech.edu/",),
        seed_urls=("https://health.gatech.edu/immunization-requirements/",),
        max_urls=60,
        vertical="health_support",
        adapter="paths",
        allowed_path_prefixes=("/immunization-requirements", "/files"),
        allowed_redirect_roots=("https://c14750.wpmucdn.com/2790/files/",),
        content_types=("text/html", "application/pdf"),
    )
    calls: list[str] = []
    captured: dict[str, FetchedDocument] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == official_url:
            return httpx.Response(302, headers={"Location": cdn_url}, request=request)
        return httpx.Response(
            200,
            content=b"fake-pdf",
            headers={"Content-Type": "application/pdf"},
            request=request,
        )

    monkeypatch.setattr(
        "ingestion.documents.sync.extract_pdf",
        lambda data: ExtractedPdf(
            "Immunization Requirements",
            (ExtractedPage(1, "Official immunization requirement information. " * 20),),
        ),
    )

    def fake_store(session, source, fetched, embed_fn):
        captured["fetched"] = fetched
        return True, 1

    monkeypatch.setattr("ingestion.documents.sync._store_document", fake_store)
    session = MagicMock()
    session.scalar.return_value = None

    result = await sync_document_url(
        source,
        official_url,
        lambda: nullcontext(session),
        lambda texts: [],
        httpx.MockTransport(handler),
    )

    assert calls == [official_url, cdn_url]
    assert result.outcome is DocumentSyncOutcome.INDEXED
    assert result.requests_used == 2
    fetched = captured["fetched"]
    assert fetched.source_url == official_url
    assert fetched.canonical_url == official_url
    assert fetched.content_type == "application/pdf"


@pytest.mark.asyncio
async def test_external_redirect_remains_blocked_without_explicit_redirect_root():
    official_url = "https://health.gatech.edu/files/form.pdf"
    source = DocumentSource(
        name="gt-health",
        source_type="health_support",
        authority="health",
        allowed_roots=("https://health.gatech.edu/",),
        seed_urls=("https://health.gatech.edu/immunization-requirements/",),
        max_urls=60,
        vertical="health_support",
        adapter="paths",
        allowed_path_prefixes=("/files",),
        content_types=("text/html", "application/pdf"),
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://cdn.example.com/files/form.pdf"},
            request=request,
        )

    session = MagicMock()
    session.scalar.return_value = None
    result = await sync_document_url(
        source,
        official_url,
        lambda: nullcontext(session),
        lambda texts: [],
        httpx.MockTransport(handler),
    )

    assert calls == [official_url]
    assert result.outcome is DocumentSyncOutcome.FETCH_FAILED
    assert result.reason == "REDIRECT_NOT_ALLOWED"
    assert result.requests_used == 1
