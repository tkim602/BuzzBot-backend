from eval.quality.reindex_gold import gold_units
from eval.quality.schema import GoldCase
from ingestion.documents.registry import DocumentSource


def _case(case_id: str, url: str) -> GoldCase:
    return GoldCase(
        id=case_id,
        variant_group=case_id.rsplit("-v", 1)[0],
        question="Question?",
        gold_answer="Answer.",
        gold_urls=(url,),
        gold_sources=("gt-test",),
        gold_vertical="academics",
        gold_locator="policy",
        question_type="policy",
        time_sensitive=False,
        difficulty="direct",
        style="direct",
    )


def test_gold_units_deduplicates_urls_and_keeps_registry_source():
    source = DocumentSource(
        "gt-test",
        "official_policy",
        "paths",
        ("https://example.gatech.edu/",),
        ("https://example.gatech.edu/start",),
        10,
    )
    url = "https://example.gatech.edu/policy"

    units = gold_units([_case("gold-001-v1", url), _case("gold-002-v1", url)], (source,))

    assert units == ((source, url),)
