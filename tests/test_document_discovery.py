import pytest

from ingestion.documents.admission import discover_urls as discover_admission_urls
from ingestion.documents.catalog import discover_urls as discover_catalog_urls
from ingestion.documents.discovery import MaxUrlsExceededError
from ingestion.documents.omscs import discover_urls as discover_omscs_urls
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


def test_omscs_discovery_accepts_only_core_policy_pages():
    source = DocumentSource(
        "gt-omscs",
        "omscs_policy",
        "omscs",
        ("https://omscs.gatech.edu/",),
        ("https://omscs.gatech.edu/admission-criteria",),
        6,
    )
    html = """
    <a href="/degree-requirements/">Degree requirements</a>
    <a href="/degree-requirements?from=nav#top">Duplicate</a>
    <a href="/current-courses">Current courses</a>
    <a href="/prospective-student-faqs">FAQs</a>
    <a href="/specializations">Specializations</a>
    <a href="/cost-and-payment-schedule">Cost</a>
    <a href="/news/example">News</a>
    <a href="/cs-7641-machine-learning">Individual course</a>
    <a href="https://example.com/degree-requirements">External</a>
    <a href="/orientation.pdf">PDF</a>
    """

    assert discover_omscs_urls(source, html) == (
        "https://omscs.gatech.edu/admission-criteria",
        "https://omscs.gatech.edu/degree-requirements",
        "https://omscs.gatech.edu/current-courses",
        "https://omscs.gatech.edu/prospective-student-faqs",
        "https://omscs.gatech.edu/specializations",
        "https://omscs.gatech.edu/cost-and-payment-schedule",
    )


def test_admission_discovery_accepts_only_direct_first_year_pages():
    source = _source("gt-admission", "https://admission.gatech.edu/first-year", 4)
    html = """
    <a href="/first-year/deadlines/">Deadlines</a>
    <a href="/first-year/deadlines?from=nav#top">Duplicate</a>
    <a href="/first-year/application-review">Application review</a>
    <a href="/first-year/personal-essays">Essays</a>
    <a href="/first-year/personal-essays/examples">Nested</a>
    <a href="/first-year/checklist.pdf">PDF</a>
    <a href="/transfer/deadlines">Transfer</a>
    <a href="/visit">Visit</a>
    <a href="https://apply.gatech.edu/apply/">Portal</a>
    """

    assert discover_admission_urls(source, html) == (
        "https://admission.gatech.edu/first-year",
        "https://admission.gatech.edu/first-year/deadlines",
        "https://admission.gatech.edu/first-year/application-review",
        "https://admission.gatech.edu/first-year/personal-essays",
    )


def test_new_adapters_fail_instead_of_truncating_over_the_ceiling():
    source = DocumentSource(
        "gt-omscs",
        "omscs_policy",
        "omscs",
        ("https://omscs.gatech.edu/",),
        ("https://omscs.gatech.edu/admission-criteria",),
        1,
    )

    with pytest.raises(MaxUrlsExceededError):
        discover_omscs_urls(source, '<a href="/degree-requirements">Degree</a>')
