from __future__ import annotations

import re

from eval.quality.metrics import normalize_url


def _rank(urls: list[object], gold_urls: list[object]) -> int | None:
    gold = {normalize_url(str(url)) for url in gold_urls}
    for index, url in enumerate(urls, start=1):
        if normalize_url(str(url)) in gold:
            return index
    return None


def _target_course_metrics(
    outputs: dict[str, object], reference_outputs: dict[str, object]
) -> dict[str, object]:
    subject = re.escape(str(reference_outputs.get("expected_subject", "")))
    number = re.escape(str(reference_outputs.get("expected_course_number", "")))
    marker = re.compile(rf"(?<![A-Z0-9]){subject}\s*{number}(?![A-Z0-9])", re.I)
    matches: list[tuple[int, str]] = []
    evidence = outputs.get("evidence", [])
    if isinstance(evidence, list):
        for rank, item in enumerate(evidence, start=1):
            if not isinstance(item, dict):
                continue
            if marker.search(f"{item.get('title') or ''}\n{item.get('text') or ''}"):
                metadata = item.get("metadata", {})
                chunk_id = metadata.get("chunk_id", "") if isinstance(metadata, dict) else ""
                matches.append((rank, str(chunk_id)))
    best_rank = matches[0][0] if matches else None
    return {
        "target_course_best_rank": best_rank,
        "target_course_chunk_hit_at_1": best_rank == 1,
        "target_course_chunk_hit_at_5": best_rank is not None and best_rank <= 5,
        "target_course_chunk_hit_at_8": best_rank is not None and best_rank <= 8,
        "target_course_mrr_at_8": 1 / best_rank
        if best_rank is not None and best_rank <= 8
        else 0.0,
        "target_course_chunk_ids": [chunk_id for _, chunk_id in matches if chunk_id],
    }


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
        **_target_course_metrics(outputs, reference_outputs),
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
