"""Large-scale BuzzBot evaluation — 300+ questions across 15+ categories.

Sends queries to the running chatbot, evaluates keyword recall,
intent accuracy, citation presence, confidence, and latency.
Handles both single-turn and multi-turn (follow-up) queries.
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

API_BASE = "http://localhost:8000"
QUESTIONS_FILE = Path(__file__).parent / "questions_300.jsonl"
RESULTS_FILE = Path(__file__).parent / "eval_300_results.json"
TIMEOUT = 90


def load_questions() -> list[dict]:
    questions = []
    with open(QUESTIONS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    return questions


def send_query(client: httpx.Client, query: str, history: list[dict] | None = None) -> tuple[dict | None, float, str | None]:
    """Send a query and return (response_json, latency, error)."""
    if not query.strip():
        return None, 0.0, "empty_query"
    start = time.time()
    try:
        payload: dict = {"query": query}
        if history:
            payload["history"] = history
        resp = client.post(f"{API_BASE}/chat", json=payload)
        elapsed = time.time() - start
        if resp.status_code == 200:
            return resp.json(), elapsed, None
        return None, elapsed, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        elapsed = time.time() - start
        return None, elapsed, str(exc)[:200]


def keyword_recall(answer: str, keywords: list[str]) -> tuple[int, int]:
    if not keywords:
        return 0, 0
    a = answer.lower()
    hits = sum(1 for kw in keywords if kw.lower() in a)
    return hits, len(keywords)


def evaluate_case(q: dict, data: dict | None, latency: float, error: str | None) -> dict:
    result = {
        "id": q["id"],
        "category": q.get("category", "unknown"),
        "query": q["query"],
        "error": error,
        "latency_s": round(latency, 2),
    }

    if error or data is None:
        result.update({"pass": False, "keyword_hits": 0, "keyword_total": 0,
                       "keyword_recall": 0.0, "intent_match": False,
                       "has_answer": False, "has_citations": False, "confidence": 0.0})
        return result

    answer = data.get("answer", "")
    debug = data.get("debug", {})
    citations = data.get("citations", [])
    confidence = data.get("confidence", 0.0)
    actual_intent = debug.get("intent", "")
    rewritten = debug.get("rewritten_query", "")

    expected_kw = q.get("expected_keywords", [])
    hits, total = keyword_recall(answer, expected_kw)
    recall = hits / total if total > 0 else 1.0

    expected_intent = q.get("expected_intent")
    intent_ok = (expected_intent == actual_intent) if expected_intent else True

    has_answer = bool(answer.strip())
    has_citations = len(citations) > 0

    # Pass if we have an answer AND (keyword recall >= 0.5 OR no keywords expected)
    passed = has_answer and (recall >= 0.5 or total == 0)

    result.update({
        "pass": passed,
        "keyword_hits": hits,
        "keyword_total": total,
        "keyword_recall": round(recall, 3),
        "intent_match": intent_ok,
        "actual_intent": actual_intent,
        "has_answer": has_answer,
        "has_citations": has_citations,
        "confidence": confidence,
        "answer_snippet": answer[:200],
        "rewritten_query": rewritten,
        "citation_count": len(citations),
    })
    return result


def run_eval() -> None:
    questions = load_questions()
    total = len(questions)
    print(f"BuzzBot Large-Scale Evaluation — {total} questions")
    print("=" * 80)

    results: list[dict] = []
    n_done = 0
    n_pass = 0
    n_fail = 0
    n_error = 0

    with httpx.Client(timeout=TIMEOUT) as client:
        # Check server health
        try:
            health = client.get(f"{API_BASE}/health")
            if health.status_code != 200:
                print("ERROR: Server not healthy. Start with: make run-backend")
                sys.exit(1)
        except Exception:
            print("ERROR: Cannot connect to server at", API_BASE)
            sys.exit(1)

        for q in questions:
            n_done += 1
            query = q["query"]
            history = q.get("history")

            data, latency, error = send_query(client, query, history)

            # Retry once on rate limit (429)
            if error and "429" in str(error):
                time.sleep(3)
                data, latency, error = send_query(client, query, history)

            r = evaluate_case(q, data, latency, error)
            results.append(r)

            # Pace requests to stay under rate limits
            time.sleep(1.0)

            if r.get("error"):
                n_error += 1
                status_char = "E"
            elif r["pass"]:
                n_pass += 1
                status_char = "."
            else:
                n_fail += 1
                status_char = "F"

            # Progress indicator
            if n_done % 10 == 0 or status_char != ".":
                pct = n_done / total * 100
                if status_char != ".":
                    print(f"  [{n_done:3d}/{total}] {status_char} {q['id']:20s} kw={r['keyword_hits']}/{r['keyword_total']} "
                          f"conf={r['confidence']:.1f} | {query[:60]}")
                else:
                    print(f"  [{n_done:3d}/{total}] {pct:5.1f}% done — {n_pass}P/{n_fail}F/{n_error}E")

    # ========================= AGGREGATE METRICS =========================
    print("\n" + "=" * 80)
    print("AGGREGATE METRICS")
    print("=" * 80)

    successful = [r for r in results if not r.get("error")]
    with_keywords = [r for r in successful if r["keyword_total"] > 0]

    # Overall
    pass_rate = n_pass / total if total else 0
    answer_rate = sum(1 for r in successful if r["has_answer"]) / total if total else 0
    citation_rate = sum(1 for r in successful if r["has_citations"]) / len(successful) if successful else 0
    intent_matches = sum(1 for r in successful if r.get("intent_match"))
    intent_rate = intent_matches / len(successful) if successful else 0

    # Keyword recall (precision proxy)
    total_kw_hits = sum(r["keyword_hits"] for r in with_keywords)
    total_kw_expected = sum(r["keyword_total"] for r in with_keywords)
    macro_recall = sum(r["keyword_recall"] for r in with_keywords) / len(with_keywords) if with_keywords else 0
    micro_recall = total_kw_hits / total_kw_expected if total_kw_expected else 0

    # Confidence
    avg_confidence = sum(r["confidence"] for r in successful) / len(successful) if successful else 0

    # Latency
    latencies = [r["latency_s"] for r in successful if r["latency_s"] > 0]
    latencies.sort()

    print(f"\nTotal questions:      {total}")
    print(f"Successful calls:     {len(successful)}")
    print(f"Errors:               {n_error}")
    print(f"")
    print(f"Pass rate:            {n_pass}/{total} ({pass_rate*100:.1f}%)")
    print(f"Answer rate:          {answer_rate*100:.1f}%")
    print(f"Intent match rate:    {intent_matches}/{len(successful)} ({intent_rate*100:.1f}%)")
    print(f"Citation rate:        {citation_rate*100:.1f}%")
    print(f"")
    print(f"Keyword recall (macro avg): {macro_recall:.3f}")
    print(f"Keyword recall (micro avg): {micro_recall:.3f}")
    print(f"Avg confidence:       {avg_confidence:.3f}")
    print(f"")
    if latencies:
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        print(f"Latency — min: {min(latencies):.1f}s  avg: {sum(latencies)/len(latencies):.1f}s  "
              f"p50: {p50:.1f}s  p95: {p95:.1f}s  max: {max(latencies):.1f}s")

    # ========================= PER-CATEGORY BREAKDOWN =========================
    print(f"\n{'CATEGORY':<25s} {'PASS':>6s} {'TOTAL':>6s} {'RATE':>7s} {'KW_REC':>7s} {'INTENT':>7s} {'CIT':>6s} {'CONF':>6s}")
    print("-" * 80)

    cat_groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        cat_groups[r["category"]].append(r)

    for cat in sorted(cat_groups.keys()):
        group = cat_groups[cat]
        cat_pass = sum(1 for r in group if r["pass"])
        cat_total = len(group)
        cat_rate = cat_pass / cat_total if cat_total else 0
        cat_kw = [r for r in group if r["keyword_total"] > 0 and not r.get("error")]
        cat_recall = sum(r["keyword_recall"] for r in cat_kw) / len(cat_kw) if cat_kw else 0
        cat_succ = [r for r in group if not r.get("error")]
        cat_intent = sum(1 for r in cat_succ if r.get("intent_match")) / len(cat_succ) if cat_succ else 0
        cat_cit = sum(1 for r in cat_succ if r.get("has_citations")) / len(cat_succ) if cat_succ else 0
        cat_conf = sum(r.get("confidence", 0) for r in cat_succ) / len(cat_succ) if cat_succ else 0
        print(f"  {cat:<23s} {cat_pass:>6d} {cat_total:>6d} {cat_rate*100:>6.1f}% {cat_recall:>6.3f} {cat_intent*100:>6.1f}% {cat_cit*100:>5.1f}% {cat_conf:>5.2f}")

    # ========================= FAILURES DETAIL =========================
    failures = [r for r in results if not r["pass"] and not r.get("error")]
    if failures:
        print(f"\n{'='*80}")
        print(f"FAILED CASES ({len(failures)})")
        print(f"{'='*80}")
        for r in failures[:30]:  # Show first 30 failures
            print(f"  [{r['id']}] kw={r['keyword_hits']}/{r['keyword_total']} "
                  f"intent={r.get('actual_intent','?')} conf={r['confidence']:.1f}")
            print(f"    Q: {r['query'][:80]}")
            print(f"    A: {r.get('answer_snippet','')[:100]}")
            print(f"    Rewrite: {r.get('rewritten_query','')[:80]}")
            print()

    # ========================= WRITE RESULTS =========================
    output = {
        "summary": {
            "total": total,
            "passed": n_pass,
            "failed": n_fail,
            "errors": n_error,
            "pass_rate": round(pass_rate, 4),
            "answer_rate": round(answer_rate, 4),
            "intent_match_rate": round(intent_rate, 4),
            "citation_rate": round(citation_rate, 4),
            "keyword_recall_macro": round(macro_recall, 4),
            "keyword_recall_micro": round(micro_recall, 4),
            "avg_confidence": round(avg_confidence, 4),
            "latency_avg_s": round(sum(latencies) / len(latencies), 2) if latencies else 0,
            "latency_p50_s": round(p50, 2) if latencies else 0,
            "latency_p95_s": round(p95, 2) if latencies else 0,
        },
        "per_category": {},
        "cases": results,
    }

    for cat in sorted(cat_groups.keys()):
        group = cat_groups[cat]
        cat_pass = sum(1 for r in group if r["pass"])
        cat_kw = [r for r in group if r["keyword_total"] > 0 and not r.get("error")]
        output["per_category"][cat] = {
            "total": len(group),
            "passed": cat_pass,
            "pass_rate": round(cat_pass / len(group), 3) if group else 0,
            "keyword_recall": round(sum(r["keyword_recall"] for r in cat_kw) / len(cat_kw), 3) if cat_kw else 0,
        }

    with open(RESULTS_FILE, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nDetailed results written to: {RESULTS_FILE}")


if __name__ == "__main__":
    run_eval()
