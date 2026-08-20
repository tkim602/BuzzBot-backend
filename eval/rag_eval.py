"""RAG evaluation — run sample questions and measure quality metrics."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

API_BASE = "http://localhost:8000"
QUESTIONS_FILE = Path(__file__).parent / "questions.sample.jsonl"


def load_questions() -> list[dict]:
    questions = []
    with open(QUESTIONS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    return questions


def evaluate_response(question: dict, response: dict) -> dict:
    """Evaluate a single response."""
    result = {
        "id": question["id"],
        "query": question["query"],
        "expected_intent": question.get("expected_intent"),
        "actual_intent": response.get("debug", {}).get("intent"),
        "intent_match": False,
        "has_citations": len(response.get("citations", [])) > 0,
        "citation_count": len(response.get("citations", [])),
        "confidence": response.get("confidence", 0),
        "has_answer": bool(response.get("answer", "").strip()),
    }

    # Check intent match
    if result["expected_intent"] and result["actual_intent"]:
        result["intent_match"] = result["expected_intent"] == result["actual_intent"]

    # Check citation validity (quotes should be non-empty)
    valid_citations = sum(1 for c in response.get("citations", []) if c.get("quote", "").strip())
    result["valid_citation_count"] = valid_citations

    return result


def run_eval() -> None:
    questions = load_questions()
    print(f"Running evaluation with {len(questions)} questions...")
    print("=" * 60)

    results: list[dict] = []
    latencies: list[float] = []

    with httpx.Client(timeout=60) as client:
        for q in questions:
            start = time.time()
            try:
                resp = client.post(
                    f"{API_BASE}/chat",
                    json={"query": q["query"]},
                )
                elapsed = time.time() - start
                latencies.append(elapsed)

                if resp.status_code == 200:
                    data = resp.json()
                    result = evaluate_response(q, data)
                    result["latency_s"] = round(elapsed, 2)
                    result["error"] = None
                else:
                    result = {
                        "id": q["id"],
                        "query": q["query"],
                        "error": f"HTTP {resp.status_code}",
                        "latency_s": round(elapsed, 2),
                    }
            except Exception as exc:
                elapsed = time.time() - start
                result = {
                    "id": q["id"],
                    "query": q["query"],
                    "error": str(exc),
                    "latency_s": round(elapsed, 2),
                }

            results.append(result)
            status = "OK" if not result.get("error") else f"ERR: {result['error']}"
            print(f"  [{result['id']}] {status} ({result['latency_s']}s)")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    total = len(results)
    successful = [r for r in results if not r.get("error")]
    print(f"Total: {total} | Success: {len(successful)} | Failed: {total - len(successful)}")

    if successful:
        intent_matches = sum(1 for r in successful if r.get("intent_match"))
        with_citations = sum(1 for r in successful if r.get("has_citations"))
        valid_cit_total = sum(r.get("valid_citation_count", 0) for r in successful)
        cit_total = sum(r.get("citation_count", 0) for r in successful)
        avg_confidence = sum(r.get("confidence", 0) for r in successful) / len(successful)

        print(
            f"Intent match rate: {intent_matches}/{len(successful)} ({intent_matches / len(successful) * 100:.0f}%)"
        )
        print(
            f"Retrieval hit rate (has citations): {with_citations}/{len(successful)} ({with_citations / len(successful) * 100:.0f}%)"
        )
        print(f"Citation validity rate: {valid_cit_total}/{cit_total if cit_total else 1}")
        print(f"Avg confidence: {avg_confidence:.2f}")

    if latencies:
        print(
            f"Latency — min: {min(latencies):.2f}s | avg: {sum(latencies) / len(latencies):.2f}s | max: {max(latencies):.2f}s"
        )

    # Write results
    out_path = Path(__file__).parent / "eval_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nDetailed results: {out_path}")


if __name__ == "__main__":
    run_eval()
