import pytest

from app.rag.retrieval import RetrievedChunk
from app.retrieval.documents import PolicyQuery, search_policy_docs


def test_policy_query_validates_text_types_and_limit():
    with pytest.raises(ValueError, match="text"):
        PolicyQuery("  ")
    with pytest.raises(ValueError, match="source type"):
        PolicyQuery("registration", source_types=("unknown",))
    with pytest.raises(ValueError, match="top_k"):
        PolicyQuery("registration", top_k=0)


@pytest.mark.asyncio
async def test_exact_deadline_uses_calendar_authority_and_preserves_citation(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_hybrid(session, query, query_embedding, **kwargs):
        captured.update(kwargs)
        return [
            RetrievedChunk(
                chunk_id="chunk-1",
                url="https://registrar.gatech.edu/current-academic-calendar",
                title="Current Academic Calendar",
                chunk_text="The deadline is August 21.",
                score=0.9,
                source_name="gt-academic-calendar",
                fetched_at="2026-08-20T00:00:00+00:00",
                metadata_json={
                    "source_type": "academic_calendar",
                    "authority": "academic_calendar",
                    "edition": "2026-2027",
                },
                method="hybrid_rrf",
            )
        ]

    monkeypatch.setattr("app.retrieval.documents.hybrid_retrieve", fake_hybrid)
    evidence = await search_policy_docs(
        object(),
        PolicyQuery("What is the exact registration deadline?"),
        [0.1] * 1536,
    )

    assert captured["source_filter"] == ["gt-academic-calendar"]
    assert evidence[0].canonical_url.endswith("current-academic-calendar")
    assert evidence[0].authority == "academic_calendar"
    assert evidence[0].edition == "2026-2027"
    assert evidence[0].retrieval_method == "hybrid_rrf"


@pytest.mark.asyncio
async def test_requested_source_types_map_to_official_sources_and_deduplicate(monkeypatch):
    captured: dict[str, object] = {}
    chunk = RetrievedChunk(
        chunk_id="same",
        url="https://catalog.gatech.edu/coursesaz/cs/",
        title="Computer Science",
        chunk_text="CS 7650 Natural Language.",
        score=0.8,
        source_name="gt-catalog",
        fetched_at="2026-08-20T00:00:00+00:00",
        metadata_json={"source_type": "course_catalog", "authority": "catalog"},
    )

    async def fake_hybrid(session, query, query_embedding, **kwargs):
        captured.update(kwargs)
        return [chunk, chunk]

    monkeypatch.setattr("app.retrieval.documents.hybrid_retrieve", fake_hybrid)
    evidence = await search_policy_docs(
        object(),
        PolicyQuery("Describe CS 7650", source_types=("course_catalog",)),
        [0.1] * 1536,
    )

    assert captured["source_filter"] == ["gt-catalog"]
    assert len(evidence) == 1


@pytest.mark.asyncio
async def test_explicit_admissions_authority_is_not_overridden_by_deadline_word(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_hybrid(session, query, query_embedding, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("app.retrieval.documents.hybrid_retrieve", fake_hybrid)
    await search_policy_docs(
        object(),
        PolicyQuery("What is the application deadline?", source_types=("admissions",)),
        [0.1] * 1536,
    )

    assert captured["source_filter"] == [
        "gt-admission",
        "gt-transfer-admission",
        "gt-graduate-admission",
    ]


@pytest.mark.asyncio
async def test_one_source_type_filters_all_registered_sources(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_hybrid(session, query, query_embedding, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("app.retrieval.documents.hybrid_retrieve", fake_hybrid)
    await search_policy_docs(
        object(),
        PolicyQuery("How do I pay tuition?", source_types=("finance",)),
        [0.1] * 1536,
    )

    assert captured["source_filter"] == ["gt-bursar", "gt-financial-aid"]


@pytest.mark.asyncio
async def test_pdf_page_metadata_is_preserved_in_document_evidence(monkeypatch):
    async def fake_hybrid(session, query, query_embedding, **kwargs):
        return [
            RetrievedChunk(
                chunk_id="pdf-page",
                url="https://housing.gatech.edu/guide.pdf",
                title="Housing Guide",
                chunk_text="Cancellation deadline is July 1.",
                score=0.9,
                source_name="gt-housing",
                fetched_at="2026-08-21T00:00:00+00:00",
                metadata_json={
                    "source_type": "housing",
                    "authority": "housing",
                    "page_start": 4,
                },
            )
        ]

    monkeypatch.setattr("app.retrieval.documents.hybrid_retrieve", fake_hybrid)
    evidence = await search_policy_docs(
        object(), PolicyQuery("What is the cancellation deadline?", source_types=("housing",)), []
    )

    assert evidence[0].page == 4
