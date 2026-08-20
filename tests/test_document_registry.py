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
    assert all(source.max_urls <= 25 for source in sources)
    assert all(source.seed_urls for source in sources)
    assert all(root.startswith("https://") for source in sources for root in source.allowed_roots)
    omscs = next(source for source in sources if source.name == "gt-omscs")
    assert omscs.allowed_roots == ("https://omscs.gatech.edu/",)
    assert omscs.seed_urls == ("https://omscs.gatech.edu/admission-criteria",)


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
