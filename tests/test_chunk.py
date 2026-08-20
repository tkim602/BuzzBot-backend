"""Tests for chunking logic."""

from ingestion.chunk import chunk_text


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
