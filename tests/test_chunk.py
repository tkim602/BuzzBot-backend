"""Tests for chunking logic."""

from ingestion.chunk import chunk_text
from ingestion.extract import extract_content


def test_short_text_single_chunk():
    result = chunk_text("This is a short text.", chunk_size=500)
    assert len(result) == 1
    assert result[0].text == "This is a short text."
    assert result[0].index == 0


def test_long_text_multiple_chunks():
    text = " ".join(["word"] * 2000)
    result = chunk_text(text, chunk_size=500, chunk_overlap=80)
    assert len(result) > 1
    # Chunks should overlap
    for r in result:
        assert r.token_count > 0


def test_chunk_hash_deterministic():
    result1 = chunk_text("Deterministic test text.", chunk_size=500)
    result2 = chunk_text("Deterministic test text.", chunk_size=500)
    assert result1[0].chunk_hash == result2[0].chunk_hash


def test_chunk_metadata_passed():
    meta = {"url": "https://example.com", "source": "test"}
    result = chunk_text("Some text here.", chunk_size=500, metadata=meta)
    assert result[0].metadata["url"] == "https://example.com"
    assert result[0].metadata["source"] == "test"


def test_min_chunk_size_filters_tiny():
    text = " ".join(["word"] * 600)
    result = chunk_text(text, chunk_size=500, chunk_overlap=80, min_chunk_size=50)
    for r in result:
        assert r.token_count >= 50


def test_heading_aware_chunking_adds_section_metadata():
    text = """
Registration
The registration period opens next week.

Add/Drop
Students can add and drop classes until the posted deadline.
"""
    result = chunk_text(text, chunk_size=80, chunk_overlap=10)
    assert len(result) >= 1
    assert any("section_heading" in r.metadata for r in result)


def test_structured_field_labels_stay_inside_explicit_heading_section():
    text = """## Event 1
Semester: Fall 2026
Category: Registration
Date: August 17 (Mon), 2026
Event: Registration opens.
""".strip()

    result = chunk_text(text, min_chunk_size=10)

    assert len(result) == 1
    assert result[0].text == text.removeprefix("## ")


def test_catalog_lists_are_content_not_one_token_headings():
    text = "\n".join(
        ["Courses", "A"]
        + [f"- Accounting Course Subject {index} (ACCT{index})" for index in range(40)]
    )

    result = chunk_text(text, min_chunk_size=50)

    assert result
    assert all(chunk.token_count >= 50 for chunk in result)
    assert "Accounting Course Subject" in result[0].text


def test_short_deadline_sections_are_merged_without_losing_dates():
    text = """## Application Plans
{}
## Early Action 1
October 15
November 2
## Regular Decision
January 6
""".format(" ".join(["application guidance"] * 60))

    result = chunk_text(text, chunk_size=100, chunk_overlap=20, min_chunk_size=50)
    indexed_text = "\n".join(chunk.text for chunk in result)

    assert "October 15" in indexed_text
    assert "November 2" in indexed_text
    assert "January 6" in indexed_text


def test_every_section_marker_survives_chunking():
    markers = ["SOURCE-MARKER-A", "SOURCE-MARKER-B", "SOURCE-MARKER-C"]
    text = "\n".join(
        (
            "## Main Policy",
            " ".join(["policy detail"] * 80),
            "## Short Table",
            markers[0],
            markers[1],
            "## Final Note",
            markers[2],
        )
    )

    chunks = chunk_text(text, chunk_size=80, chunk_overlap=10, min_chunk_size=50)

    assert all(any(marker in chunk.text for chunk in chunks) for marker in markers)


def test_html_table_relationships_survive_chunking():
    html = """
    <html><head><title>First-Year Deadlines</title></head><body>
      <p>Official application deadline information for first-year applicants.</p>
      <p>This page explains the available application plans and their deadlines.</p>
      <table>
        <thead><tr><th>Important Dates</th><th>Early Action 1</th><th>Early Action 2</th><th>Regular Decision</th></tr></thead>
        <tbody><tr><td>Application Deadline</td><td>October 15</td><td>November 2</td><td>January 6</td></tr></tbody>
      </table>
    </body></html>
    """

    extracted = extract_content("https://example.gatech.edu/deadlines", html)
    indexed = "\n".join(chunk.text for chunk in chunk_text(extracted.text, min_chunk_size=10))

    assert "Early Action 1 — Application Deadline: October 15" in indexed
    assert "Early Action 2 — Application Deadline: November 2" in indexed
    assert "Regular Decision — Application Deadline: January 6" in indexed
