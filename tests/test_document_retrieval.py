from unittest.mock import AsyncMock

import pytest

from app.rag.retrieval import RetrievedChunk
from app.retrieval.documents import OFFICIAL_SOURCE_NAMES, PolicyQuery, search_policy_docs


@pytest.fixture(autouse=True)
def disable_child_reselection(monkeypatch):
    monkeypatch.setattr(
        "app.retrieval.documents.vector_search", AsyncMock(return_value=[]), raising=False
    )


def test_policy_query_validates_text_types_and_limit():
    with pytest.raises(ValueError, match="text"):
        PolicyQuery("  ")
    with pytest.raises(ValueError, match="source type"):
        PolicyQuery("registration", source_types=("unknown",))
    with pytest.raises(ValueError, match="top_k"):
        PolicyQuery("registration", top_k=0)


@pytest.mark.asyncio
async def test_policy_reselects_children_within_discovered_urls(monkeypatch):
    root = RetrievedChunk(
        "root",
        "https://example.gatech.edu/policy",
        "Policy",
        "General policy overview.",
        0.9,
        source_name="gt-registrar",
        metadata_json={"source_type": "official_policy", "authority": "registrar"},
    )
    children = [
        RetrievedChunk(
            chunk_id,
            url,
            "Policy",
            text,
            score,
            source_name="gt-registrar",
            metadata_json={"source_type": "official_policy", "authority": "registrar"},
        )
        for chunk_id, url, text, score in (
            ("answer-1", root.url, "The direct answer.", 0.95),
            ("answer-2", root.url, "A supporting condition.", 0.9),
            ("duplicate-3", root.url, "A third section from the same page.", 0.85),
            ("other", "https://example.gatech.edu/other", "Another page.", 0.8),
        )
    ]
    captured: dict[str, object] = {}

    async def fake_hybrid(*args, **kwargs):
        return [
            root,
            RetrievedChunk(
                "other-root",
                "https://example.gatech.edu/other",
                "Other",
                "Other overview.",
                0.8,
                source_name="gt-registrar",
                metadata_json={"source_type": "official_policy", "authority": "registrar"},
            ),
        ]

    async def fake_vector(*args, **kwargs):
        captured.update(kwargs)
        return children

    monkeypatch.setattr("app.retrieval.documents.hybrid_retrieve", fake_hybrid)
    monkeypatch.setattr("app.retrieval.documents.vector_search", fake_vector, raising=False)

    evidence = await search_policy_docs(
        object(), PolicyQuery("What is the direct policy?", top_k=3), [0.1] * 1536
    )

    assert captured["url_filter"] == [root.url, "https://example.gatech.edu/other"]
    assert captured["similarity_threshold"] == -1.0
    assert [item.chunk_id for item in evidence] == ["answer-1", "answer-2", "other"]


@pytest.mark.asyncio
async def test_policy_reselection_keeps_high_confidence_lexical_evidence(monkeypatch):
    def chunk(chunk_id, url, title, text, score):
        return RetrievedChunk(
            chunk_id,
            url,
            title,
            text,
            score,
            source_name="gt-catalog-rules",
            metadata_json={"source_type": "academic_policy", "authority": "catalog"},
        )

    anchor = chunk(
        "pass-fail",
        "https://example.gatech.edu/policies/pass-fail-system-rules",
        "Pass/Fail System Rules",
        "No more than nine pass/fail credits count toward an undergraduate degree.",
        0.4,
    )
    other_root = chunk(
        "other-root", "https://example.gatech.edu/rules", "Academic Rules", "Rules.", 0.9
    )
    children = [
        chunk("generic-1", other_root.url, other_root.title, "Undergraduate degree rules.", 0.99),
        chunk("generic-2", other_root.url, other_root.title, "Credit counting rules.", 0.98),
        anchor,
    ]

    async def fake_hybrid(*args, **kwargs):
        return [anchor, other_root]

    async def fake_vector(*args, **kwargs):
        return children

    monkeypatch.setattr("app.retrieval.documents.hybrid_retrieve", fake_hybrid)
    monkeypatch.setattr("app.retrieval.documents.vector_search", fake_vector, raising=False)

    evidence = await search_policy_docs(
        object(),
        PolicyQuery(
            "How many pass/fail credits count toward an undergraduate degree?",
            top_k=2,
        ),
        [0.1] * 1536,
    )

    assert evidence[0].chunk_id == anchor.chunk_id


@pytest.mark.asyncio
async def test_course_code_anchor_survives_same_url_child_reselection(monkeypatch):
    def chunk(chunk_id, text, score):
        return RetrievedChunk(
            chunk_id,
            "https://catalog.gatech.edu/coursesaz/cs/",
            "Computer Science (CS)",
            text,
            score,
            source_name="gt-catalog",
            metadata_json={"source_type": "course_catalog", "authority": "catalog"},
        )

    target = chunk(
        "cs-6300",
        "CS 6300. Software Development Process. 3 Credit Hours.",
        0.4,
    )
    children = [
        chunk("cs-2316", "CS 2316. Data Input and Manipulation.", 0.99),
        chunk("cs-2340", "CS 2340. Objects and Design.", 0.98),
        target,
    ]
    monkeypatch.setattr("app.retrieval.documents.hybrid_retrieve", AsyncMock(return_value=[target]))
    monkeypatch.setattr(
        "app.retrieval.documents.vector_search", AsyncMock(return_value=children), raising=False
    )

    evidence = await search_policy_docs(
        object(),
        PolicyQuery(
            "CS 6300 course description credits prerequisites",
            source_types=("course_catalog",),
            top_k=2,
        ),
        [0.1] * 1536,
    )

    assert evidence[0].chunk_id == target.chunk_id


@pytest.mark.asyncio
async def test_duration_question_preserves_top_reranked_evidence(monkeypatch):
    def chunk(chunk_id, text, score):
        return RetrievedChunk(
            chunk_id,
            "https://example.gatech.edu/privacy",
            "Privacy Rights",
            text,
            score,
            source_name="gt-registrar-lifecycle",
            metadata_json={"source_type": "academic_lifecycle", "authority": "registrar"},
        )

    decisive = chunk("forty-five-days", "Records may be inspected within forty-five days.", 4.9)
    children = [
        chunk("generic-1", "Education records may be disclosed in some circumstances.", 0.9),
        chunk("generic-2", "Directory information is defined by FERPA.", 0.8),
        chunk("forty-five-days", decisive.chunk_text, 0.5),
    ]
    monkeypatch.setattr(
        "app.retrieval.documents.hybrid_retrieve", AsyncMock(return_value=[decisive])
    )
    monkeypatch.setattr(
        "app.retrieval.documents.vector_search", AsyncMock(return_value=children), raising=False
    )

    evidence = await search_policy_docs(
        object(), PolicyQuery("How long can record inspection take?", top_k=2), [0.1] * 1536
    )

    assert evidence[0].chunk_id == decisive.chunk_id


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
        PolicyQuery(
            "What is the exact registration deadline?",
            source_types=("academic_calendar",),
        ),
        [0.1] * 1536,
    )

    assert captured["source_filter"] == ["gt-academic-calendar"]
    assert evidence[0].canonical_url.endswith("current-academic-calendar")
    assert evidence[0].authority == "academic_calendar"
    assert evidence[0].edition == "2026-2027"
    assert evidence[0].retrieval_method == "hybrid_rrf"


@pytest.mark.parametrize(
    "question",
    (
        "When are degrees awarded after commencement?",
        "What is the tuition payment deadline?",
        "When do unused Dining Dollars expire?",
    ),
)
@pytest.mark.asyncio
async def test_generic_deadline_policy_does_not_force_calendar_source(monkeypatch, question):
    captured: dict[str, object] = {}

    async def fake_hybrid(session, query, query_embedding, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("app.retrieval.documents.hybrid_retrieve", fake_hybrid)
    await search_policy_docs(object(), PolicyQuery(question), [0.1] * 1536)

    assert captured["source_filter"] == OFFICIAL_SOURCE_NAMES


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
    assert captured["max_chunks_per_url"] == 1
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
