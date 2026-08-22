from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from pathlib import Path

import httpx
import structlog

from app.core.usage import UsageLimitExceeded, get_usage
from app.rag.answerer import _call_llm, _extract_json
from eval.quality.metrics import normalize_url
from eval.quality.schema import GoldCase, load_manifest_cases

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


async def judge_answer(case: GoldCase, response: dict[str, object]) -> dict[str, object]:
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
                citation.get("quote", "") for citation in citations if isinstance(citation, dict)
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


async def evaluate_case(case: GoldCase, client: httpx.AsyncClient) -> dict[str, object]:
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
            sum(bool(row.get("supported") and row.get("citations")) for row in rows) / len(rows)
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
        sum(float(row.get("cost_usd") or 0.0) for row in results) if usage_valid else None
    )
    for key in ("input_tokens", "output_tokens", "embedding_tokens", "total_tokens"):
        summary[key] = sum(int(row.get(key, 0) or 0) for row in results) if usage_valid else None
    return summary


def _usage_delta(before: dict[str, object], after: dict[str, object]) -> dict[str, object]:
    before_history = list(before.get("history", []))
    after_history = list(after.get("history", []))
    if before_history:
        marker = before_history[-1]
        matches = [index for index, entry in enumerate(after_history) if entry == marker]
        if not matches:
            return {
                "usage_attribution_valid": False,
                "cost_usd": None,
                "input_tokens": None,
                "output_tokens": None,
                "embedding_tokens": None,
                "total_tokens": None,
            }
        entries = after_history[matches[-1] + 1 :]
    else:
        entries = after_history

    def tokens(usage_type: str) -> int:
        return sum(
            int(entry.get("tokens", 0)) for entry in entries if entry.get("type") == usage_type
        )

    input_tokens = tokens("input")
    output_tokens = tokens("output")
    embedding_tokens = tokens("embedding")
    return {
        "usage_attribution_valid": True,
        "cost_usd": sum(float(entry.get("cost", 0.0)) for entry in entries),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "embedding_tokens": embedding_tokens,
        "total_tokens": input_tokens + output_tokens + embedding_tokens,
    }


def _read_results(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _append_result(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def _failed_row(case: GoldCase, error: str) -> dict[str, object]:
    return {
        **_case_fields(case),
        "status": "FAILED",
        "error": error,
        "answer": "",
        "citations": [],
        "confidence": 0.0,
        "abstained": False,
        "correct": False,
        "supported": False,
        "judgment": None,
        "citation_gold_hit": False,
        "latency_ms": 0.0,
    }


def _write_reports(report_dir: Path, report: dict[str, object]) -> None:
    (report_dir / "latest_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    metrics = report["metrics"]
    cost = metrics["total_cost_usd"]
    cost_label = f"${cost:.6f}" if isinstance(cost, int | float) else "unavailable"
    markdown = "\n".join(
        [
            "# BuzzBot `/v2/chat` quality report",
            "",
            f"- Planned: {report['planned']}",
            f"- Completed: {report['completed']}",
            f"- Remaining: {report['remaining']}",
            f"- Stop reason: {report['stop_reason'] or 'none'}",
            f"- Answer correctness: {metrics['answer_correctness']:.2%}",
            f"- Evidence support: {metrics['evidence_support_rate']:.2%}",
            f"- Supported and cited: {metrics['supported_cited_answer_rate']:.2%}",
            f"- Abstention rate: {metrics['abstention_rate']:.2%}",
            "- Unsafe confident answers: not scored until baseline threshold is frozen",
            f"- Gold citation hit: {metrics['citation_gold_url_hit_rate']:.2%}",
            f"- Cost: {cost_label}",
            f"- Input tokens: {metrics['input_tokens']}",
            f"- Output tokens: {metrics['output_tokens']}",
            f"- Total tokens: {metrics['total_tokens']}",
            "",
        ]
    )
    (report_dir / "latest_summary.md").write_text(markdown, encoding="utf-8")


async def run(
    manifest: Path,
    base_url: str,
    report_dir: Path,
    *,
    force: bool = False,
    limit: int | None = None,
    min_interval: float = 2.6,
) -> dict[str, object]:
    cases = load_manifest_cases(manifest)
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        cases = cases[:limit]
    report_dir.mkdir(parents=True, exist_ok=True)
    results_path = report_dir / "latest_cases.jsonl"
    if force:
        results_path.write_text("", encoding="utf-8")
    latest = {str(row["case_id"]): row for row in _read_results(results_path) if row.get("case_id")}
    completed = {case_id for case_id, row in latest.items() if row.get("status") == "COMPLETED"}
    stop_reason = None
    last_started = 0.0

    async with httpx.AsyncClient(base_url=base_url, timeout=120) as client:
        for case in cases:
            if case.id in completed:
                continue
            wait = max(0.0, min_interval - (time.monotonic() - last_started))
            if wait:
                await asyncio.sleep(wait)
            last_started = time.monotonic()
            before = get_usage()
            try:
                row = await evaluate_case(case, client)
            except Exception as exc:
                row = _failed_row(case, type(exc).__name__)
            row.update(_usage_delta(before, get_usage()))
            _append_result(results_path, row)
            latest[case.id] = row
            if row["status"] in {
                "CHAT_BUDGET_EXHAUSTED",
                "JUDGE_BUDGET_EXHAUSTED",
            }:
                stop_reason = str(row["status"])
                break

    relevant = [latest[case.id] for case in cases if case.id in latest]
    summary = summarize_results(relevant)
    completed_count = sum(row.get("status") == "COMPLETED" for row in relevant)
    report = {
        "benchmark": manifest.stem,
        "manifest": str(manifest),
        "planned": len(cases),
        "completed": completed_count,
        "remaining": len(cases) - completed_count,
        "stop_reason": stop_reason,
        "metrics": summary,
    }
    _write_reports(report_dir, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run BuzzBot /v2/chat quality evaluation")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--report-dir", type=Path, default=Path("eval/quality/reports_chat"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--min-interval", type=float, default=2.6)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = asyncio.run(
        run(
            args.manifest,
            args.base_url,
            args.report_dir,
            force=args.force,
            limit=args.limit,
            min_interval=args.min_interval,
        )
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["remaining"] == 0 and report["stop_reason"] is None else 2


if __name__ == "__main__":
    raise SystemExit(main())
