from __future__ import annotations

import json
import math
import statistics
import time

import httpx
import structlog

from app.core.usage import UsageLimitExceeded
from app.rag.answerer import _call_llm, _extract_json
from eval.quality.metrics import normalize_url
from eval.quality.schema import GoldCase

logger = structlog.get_logger(__name__)

_ABSTENTION_NOTE = "Strict cite-or-abstain policy applied."


def _case_fields(case: GoldCase) -> dict[str, object]:
    return {
        "case_id": case.id,
        "variant_group": case.variant_group,
        "question": case.question,
        "gold_answer": case.gold_answer,
        "gold_urls": list(case.gold_urls),
        "vertical": case.gold_vertical,
        "question_type": case.question_type,
        "style": case.style,
        "time_sensitive": case.time_sensitive,
        "difficulty": case.difficulty,
    }


def _is_abstention(response: dict[str, object]) -> bool:
    notes = response.get("notes", [])
    return isinstance(notes, list) and _ABSTENTION_NOTE in notes


async def judge_answer(
    case: GoldCase, response: dict[str, object]
) -> dict[str, object]:
    system = """You are a strict RAG evaluator. Use only the supplied gold answer and citation quotes.
Return JSON only: {"verdict":"CORRECT|INCORRECT|INSUFFICIENT","supported":true|false,"reason":"short reason"}.
CORRECT means the answer materially matches the gold answer without contradiction.
supported is true only when the material answer claims are supported by the citation quotes.
Missing, unrelated, or contradictory evidence is not support. Do not use outside knowledge."""
    citations = response.get("citations", [])
    user = json.dumps(
        {
            "question": case.question,
            "gold_answer": case.gold_answer,
            "answer": response.get("answer", ""),
            "citation_quotes": [
                citation.get("quote", "")
                for citation in citations
                if isinstance(citation, dict)
            ]
            if isinstance(citations, list)
            else [],
        },
        ensure_ascii=False,
    )
    try:
        raw = await _call_llm(system, user, temperature=0.0, max_tokens=128)
        payload = _extract_json(raw)
        verdict = str(payload.get("verdict", "")).upper()
        supported = payload.get("supported")
        if verdict not in {"CORRECT", "INCORRECT", "INSUFFICIENT"} or not isinstance(
            supported, bool
        ):
            raise ValueError("malformed judge response")
        return {
            "verdict": verdict,
            "supported": supported,
            "reason": str(payload.get("reason", "")),
        }
    except UsageLimitExceeded:
        raise
    except Exception as exc:
        logger.debug("chat evaluation judge failed", error=type(exc).__name__)
        return {
            "verdict": "ERROR",
            "supported": False,
            "reason": "judge failed closed",
        }


async def evaluate_case(
    case: GoldCase, client: httpx.AsyncClient
) -> dict[str, object]:
    started = time.perf_counter()
    response = await client.post(
        "/v2/chat",
        json={"query": case.question, "thread_id": f"eval-{case.id}"},
    )
    if (
        response.status_code == 429
        and response.json().get("detail", {}).get("error") == "usage_limit_exceeded"
    ):
        return {
            **_case_fields(case),
            "status": "CHAT_BUDGET_EXHAUSTED",
            "answer": "",
            "citations": [],
            "confidence": 0.0,
            "notes": [],
            "abstained": False,
            "correct": False,
            "supported": False,
            "judgment": None,
            "citation_gold_hit": False,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    response.raise_for_status()
    body = response.json()
    citations = body.get("citations", [])
    gold_urls = {normalize_url(url) for url in case.gold_urls}
    citation_hit = any(
        normalize_url(str(citation.get("url", ""))) in gold_urls
        for citation in citations
        if isinstance(citation, dict)
    )
    abstained = _is_abstention(body)
    try:
        judged = (
            {
                "verdict": "ABSTAINED",
                "supported": False,
                "reason": "answerable gold case abstained",
            }
            if abstained
            else await judge_answer(case, body)
        )
    except UsageLimitExceeded:
        return {
            **_case_fields(case),
            "status": "JUDGE_BUDGET_EXHAUSTED",
            "answer": body.get("answer", ""),
            "citations": citations,
            "confidence": float(body.get("confidence", 0.0)),
            "notes": body.get("notes", []),
            "abstained": abstained,
            "correct": False,
            "supported": False,
            "judgment": None,
            "citation_gold_hit": citation_hit,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    status = "JUDGE_FAILED" if judged["verdict"] == "ERROR" else "COMPLETED"
    return {
        **_case_fields(case),
        "status": status,
        "answer": body.get("answer", ""),
        "citations": citations,
        "confidence": float(body.get("confidence", 0.0)),
        "notes": body.get("notes", []),
        "abstained": abstained,
        "correct": judged["verdict"] == "CORRECT",
        "supported": judged["supported"],
        "judgment": judged,
        "citation_gold_hit": citation_hit,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _ratio(rows: list[dict[str, object]], key: str) -> float:
    return sum(bool(row.get(key)) for row in rows) / len(rows) if rows else 0.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def _basic_metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    latencies = [float(row.get("latency_ms", 0.0)) for row in rows]
    confidences = [float(row.get("confidence", 0.0)) for row in rows]
    return {
        "cases": len(rows),
        "answer_correctness": _ratio(rows, "correct"),
        "evidence_support_rate": _ratio(rows, "supported"),
        "supported_cited_answer_rate": (
            sum(bool(row.get("supported") and row.get("citations")) for row in rows)
            / len(rows)
            if rows
            else 0.0
        ),
        "abstention_rate": _ratio(rows, "abstained"),
        "correct_abstention_rate": None,
        "confidence_threshold": None,
        "unsafe_confident_answer_rate": None,
        "citation_gold_url_hit_rate": _ratio(rows, "citation_gold_hit"),
        "confidence": {
            "p50": statistics.median(confidences) if confidences else 0.0,
            "p95": _percentile(confidences, 0.95),
        },
        "latency_ms": {
            "p50": statistics.median(latencies) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
        },
    }


def summarize_results(results: list[dict[str, object]]) -> dict[str, object]:
    rows = [row for row in results if row.get("status") == "COMPLETED"]
    summary = _basic_metrics(rows)
    breakdowns: dict[str, dict[str, object]] = {}
    for key in (
        "vertical",
        "question_type",
        "difficulty",
        "style",
        "time_sensitive",
    ):
        buckets: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            buckets.setdefault(str(row.get(key, "unknown")), []).append(row)
        breakdowns[key] = {
            value: _basic_metrics(bucket) for value, bucket in sorted(buckets.items())
        }
    summary["breakdowns"] = breakdowns
    summary["attempted_cases"] = len(results)
    usage_valid = all(row.get("usage_attribution_valid", False) for row in results)
    summary["usage_attribution_valid"] = usage_valid
    summary["total_cost_usd"] = (
        sum(float(row.get("cost_usd") or 0.0) for row in results)
        if usage_valid
        else None
    )
    for key in ("input_tokens", "output_tokens", "embedding_tokens", "total_tokens"):
        summary[key] = (
            sum(int(row.get(key, 0) or 0) for row in results) if usage_valid else None
        )
    return summary
