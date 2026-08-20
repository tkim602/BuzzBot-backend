import pytest

from ingestion.documents.catalog import discover_urls as discover_catalog_urls
from ingestion.documents.discovery import MaxUrlsExceededError
from ingestion.documents.registrar import discover_urls as discover_registrar_urls
from ingestion.documents.registry import DocumentSource


def _source(name: str, seed: str, max_urls: int) -> DocumentSource:
    return DocumentSource(
        name,
        "official_policy",
        name,
        (seed,),
        (seed,),
        max_urls,
    )


def test_registrar_discovery_is_bounded_canonical_and_allowlisted():
    source = _source("gt-registrar", "https://registrar.gatech.edu/registration", 4)
    html = """
    <a href="/registration/registration-assistance#help">Assistance</a>
    <a href="https://registrar.gatech.edu/registration/registration-assistance?src=nav">Duplicate</a>
    <a href="/about">About</a>
    <a href="https://example.com/registration/holds">External</a>
    <a href="/registration/holds/">Holds</a>
    <a href="/registration/waitlists">Past cap</a>
    """

    assert discover_registrar_urls(source, html) == (
        "https://registrar.gatech.edu/registration",
        "https://registrar.gatech.edu/registration/registration-assistance",
        "https://registrar.gatech.edu/registration/holds",
        "https://registrar.gatech.edu/registration/waitlists",
    )


def test_catalog_discovery_accepts_only_direct_course_subject_pages():
    source = _source("gt-catalog", "https://catalog.gatech.edu/coursesaz/", 4)
    html = """
    <a href="/coursesaz/cs/#courseinventory">Computer Science</a>
    <a href="/coursesaz/cs/?edition=2026">Duplicate</a>
    <a href="/coursesaz/cs/special/">Nested</a>
    <a href="/programs/">Programs</a>
    <a href="https://catalog.gatech.edu/coursesaz/ece/">ECE</a>
    <a href="/coursesaz/math/">Past cap</a>
    """

    assert discover_catalog_urls(source, html) == (
        "https://catalog.gatech.edu/coursesaz",
        "https://catalog.gatech.edu/coursesaz/cs",
        "https://catalog.gatech.edu/coursesaz/ece",
        "https://catalog.gatech.edu/coursesaz/math",
    )


def test_max_urls_is_a_failure_ceiling_not_silent_truncation():
    source = _source("gt-registrar", "https://registrar.gatech.edu/registration", 2)
    html = """
    <a href="/registration/holds">Holds</a>
    <a href="/registration/waitlists">Waitlists</a>
    """

    with pytest.raises(MaxUrlsExceededError):
        discover_registrar_urls(source, html)
