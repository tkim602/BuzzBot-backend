from __future__ import annotations

from eval.quality.metrics import normalize_url


def _rank(urls: list[object], gold_urls: list[object]) -> int | None:
    gold = {normalize_url(str(url)) for url in gold_urls}
    for index, url in enumerate(urls, start=1):
        if normalize_url(str(url)) in gold:
            return index
    return None


def score_stages(
    outputs: dict[str, object], reference_outputs: dict[str, object]
) -> dict[str, object]:
    returned_urls = list(outputs.get("returned_urls", []))
    gold_urls = list(reference_outputs.get("gold_urls", []))
    rank = _rank(returned_urls, gold_urls)
    citations = outputs.get("citations", [])
    citation_urls = (
        [citation.get("url", "") for citation in citations if isinstance(citation, dict)]
        if isinstance(citations, list)
        else []
    )
    return {
        "route_correct": outputs.get("intent") == reference_outputs.get("expected_route"),
        "subject_correct": outputs.get("subject") == reference_outputs.get("expected_subject"),
        "course_number_correct": (
            outputs.get("course_number") == reference_outputs.get("expected_course_number")
        ),
        "slots_correct": (
            outputs.get("subject") == reference_outputs.get("expected_subject")
            and outputs.get("course_number") == reference_outputs.get("expected_course_number")
        ),
        "best_gold_rank": rank,
        "gold_url_hit_at_5": rank is not None and rank <= 5,
        "gold_url_hit_at_8": rank is not None and rank <= 8,
        "retrieved_count": len(returned_urls),
        "retry_used": bool(outputs.get("retry_count", 0)),
        "evidence_valid": bool(outputs.get("evidence_valid")),
        "unnecessary_evidence_reject": bool(rank is not None and not outputs.get("evidence_valid")),
        "citation_gold_url_hit": _rank(citation_urls, gold_urls) is not None,
        "abstained": bool(outputs.get("abstain_reason")),
        "answer_validation_rejected": (outputs.get("abstain_reason") == "ANSWER_VALIDATION_FAILED"),
        "answer_valid": bool(outputs.get("answer_valid")),
    }


def stage_evaluator(
    outputs: dict[str, object], reference_outputs: dict[str, object]
) -> dict[str, list[dict[str, object]]]:
    scores = score_stages(outputs, reference_outputs)
    return {
        "results": [
            {"key": key, "score": value}
            for key, value in scores.items()
            if isinstance(value, bool | int | float)
        ]
    }
