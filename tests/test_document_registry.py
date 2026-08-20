from pathlib import Path

import pytest

from ingestion.documents.registry import DocumentSource, load_document_sources


def test_registry_contains_only_bounded_authoritative_sources():
    sources = load_document_sources(Path("ingestion/sources.yaml"))

    assert {source.name for source in sources} == {
        "gt-registrar",
        "gt-academic-calendar",
        "gt-catalog",
        "gt-omscs",
        "gt-admission",
    }
    assert all(source.max_urls <= 500 for source in sources)
    assert all(source.seed_urls for source in sources)
    assert all(root.startswith("https://") for source in sources for root in source.allowed_roots)
    omscs = next(source for source in sources if source.name == "gt-omscs")
    assert omscs.allowed_roots == ("https://omscs.gatech.edu/",)
    assert len(omscs.seed_urls) == 6
    assert omscs.seed_urls[0] == "https://omscs.gatech.edu/admission-criteria"
    admission = next(source for source in sources if source.name == "gt-admission")
    assert admission.allowed_roots == ("https://admission.gatech.edu/first-year/",)
    assert len(admission.seed_urls) == 7
    assert admission.seed_urls[0] == "https://admission.gatech.edu/first-year/"
    assert admission.max_urls == 30
    registrar = next(source for source in sources if source.name == "gt-registrar")
    catalog = next(source for source in sources if source.name == "gt-catalog")
    assert registrar.max_urls == 50
    assert catalog.max_urls == 150
    assert catalog.seed_urls == ("https://catalog.gatech.edu/coursesaz/",)


def test_document_source_rejects_seed_outside_allowed_roots():
    with pytest.raises(ValueError, match="allowed root"):
        DocumentSource(
            name="bad",
            source_type="policy",
            authority="registrar",
            allowed_roots=("https://registrar.gatech.edu/",),
            seed_urls=("https://evil.example/registration",),
            max_urls=1,
        )
