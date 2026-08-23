from __future__ import annotations

FailureStage = str


def classify_failure(result: dict[str, object]) -> FailureStage:
    if not result.get("route_correct"):
        return "ROUTING_ERROR"
    if not result.get("slots_correct"):
        return "SLOT_ERROR"
    if not result.get("gold_in_corpus", True):
        return "CORPUS_OR_SOURCE_MISSING"
    if result.get("pre_rerank_gold_rank") is not None and result.get("best_gold_rank") is None:
        return "RERANK_LOSS"
    if result.get("best_gold_rank") is None:
        return "RETRIEVAL_MISS"
    if not result.get("evidence_valid"):
        return "EVIDENCE_REJECT"
    if result.get("answer_validation_rejected"):
        return "ANSWER_VALIDATION_REJECT"
    if not result.get("answer_correct", True):
        return "SYNTHESIS_WRONG"
    if not result.get("answer_valid"):
        return "ANSWER_VALIDATION_REJECT"
    if result.get("judge_or_gold_issue"):
        return "JUDGE_OR_GOLD_ISSUE"
    return "PASS"
