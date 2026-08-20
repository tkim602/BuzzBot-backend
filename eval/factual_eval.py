"""Factual evaluation — send queries to BuzzBot, compare answers to ground truth."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

API_BASE = "http://localhost:8000"

# Ground-truth test cases with expected facts (verified from GT sources)
TEST_CASES = [
    # --- Previously failing: follow-up / implicit context ---
    {
        "id": "fu1",
        "category": "follow_up",
        "turns": [
            {"query": "Tell me about CS 1332"},
            {"query": "offered in spring 2025?"},
        ],
        "expected_keywords": ["CS 1332", "spring", "2025"],
        "description": "Follow-up about CS 1332 schedule without repeating course code",
    },
    {
        "id": "fu2",
        "category": "follow_up",
        "turns": [
            {"query": "What are some database courses at Georgia Tech?"},
            {"query": "another database course?"},
        ],
        "expected_keywords": ["database", "course"],
        "description": "Follow-up asking for more results on same topic",
    },
    {
        "id": "fu3",
        "category": "follow_up",
        "turns": [
            {"query": "Is CS 4400 offered in fall 2025?"},
            {"query": "then which semester is it available?"},
        ],
        "expected_keywords": ["CS 4400"],
        "description": "Implicit follow-up with 'then which semester'",
    },
    # --- Broad topic queries ---
    {
        "id": "bq1",
        "category": "broad_query",
        "turns": [{"query": "What database courses does Georgia Tech offer?"}],
        "expected_keywords": ["CS 4400", "database"],
        "description": "Broad topic query — database courses",
    },
    {
        "id": "bq2",
        "category": "broad_query",
        "turns": [{"query": "What CS courses are available for spring 2025?"}],
        "expected_keywords": ["CS"],
        "description": "Broad query about CS course offerings",
    },
    # --- Course-specific (should still work) ---
    {
        "id": "cs1",
        "category": "course_specific",
        "turns": [{"query": "What are the prerequisites for CS 1332?"}],
        "expected_keywords": ["CS 1331", "prerequisite"],
        "fact_note": "CS 1332 prereq is CS 1331 (min grade C)",
    },
    {
        "id": "cs2",
        "category": "course_specific",
        "turns": [{"query": "How many credit hours is CS 2110?"}],
        "expected_keywords": ["4", "credit"],
        "fact_note": "CS 2110 is 4 credit hours",
    },
    {
        "id": "cs3",
        "category": "course_specific",
        "turns": [{"query": "What is CS 4400 Introduction to Database Systems about?"}],
        "expected_keywords": ["database"],
        "fact_note": "CS 4400 covers database concepts, SQL, relational model",
    },
    # --- Registrar / calendar ---
    {
        "id": "rc1",
        "category": "registrar",
        "turns": [{"query": "When is the registration deadline for Fall 2025?"}],
        "expected_keywords": ["registration", "fall", "2025"],
        "description": "Calendar query - registration deadline",
    },
    {
        "id": "rc2",
        "category": "registrar",
        "turns": [{"query": "When does spring break start in 2025?"}],
        "expected_keywords": ["spring", "break"],
        "description": "Calendar query - spring break",
    },
    # --- Schedule queries ---
    {
        "id": "sc1",
        "category": "schedule",
        "turns": [{"query": "Is CS 1301 offered in Spring 2025?"}],
        "expected_keywords": ["CS 1301", "spring", "2025"],
        "fact_note": "CS 1301 Intro to Computing is typically offered every semester",
    },
    {
        "id": "sc2",
        "category": "schedule",
        "turns": [{"query": "What sections of MATH 1554 are available?"}],
        "expected_keywords": ["MATH 1554", "section"],
        "fact_note": "MATH 1554 Linear Algebra has many sections",
    },
]


@dataclass
class TurnResult:
    query: str
    answer: str = ""
    confidence: float = 0.0
    intent: str = ""
    rewritten_query: str = ""
    citation_count: int = 0
    latency_s: float = 0.0
    error: str | None = None


@dataclass
class CaseResult:
    id: str
    category: str
    description: str
    turns: list[TurnResult] = field(default_factory=list)
    keyword_hits: int = 0
    keyword_total: int = 0
    precision: float = 0.0  # keywords found / keywords expected
    has_answer: bool = False
    has_citations: bool = False
    pass_: bool = False


def check_keywords(answer: str, keywords: list[str]) -> tuple[int, int]:
    """Count how many expected keywords appear in the answer."""
    answer_lower = answer.lower()
    hits = sum(1 for kw in keywords if kw.lower() in answer_lower)
    return hits, len(keywords)


def run_case(client: httpx.Client, case: dict) -> CaseResult:
    turns = case["turns"]
    history: list[dict] = []
    turn_results: list[TurnResult] = []

    for turn in turns:
        query = turn["query"]
        start = time.time()
        try:
            resp = client.post(
                f"{API_BASE}/chat",
                json={"query": query, "history": history},
            )
            elapsed = time.time() - start
            if resp.status_code == 200:
                data = resp.json()
                tr = TurnResult(
                    query=query,
                    answer=data.get("answer", ""),
                    confidence=data.get("confidence", 0),
                    intent=data.get("debug", {}).get("intent", ""),
                    rewritten_query=data.get("debug", {}).get("rewritten_query", ""),
                    citation_count=len(data.get("citations", [])),
                    latency_s=round(elapsed, 2),
                )
            else:
                tr = TurnResult(
                    query=query, error=f"HTTP {resp.status_code}", latency_s=round(elapsed, 2)
                )
        except Exception as exc:
            elapsed = time.time() - start
            tr = TurnResult(query=query, error=str(exc), latency_s=round(elapsed, 2))

        turn_results.append(tr)
        # Build history for follow-up turns
        history.append({"role": "user", "content": query})
        if tr.answer:
            history.append({"role": "assistant", "content": tr.answer})

    # Evaluate final turn answer against expected keywords
    last = turn_results[-1]
    expected_kw = case.get("expected_keywords", [])
    hits, total = check_keywords(last.answer, expected_kw)

    cr = CaseResult(
        id=case["id"],
        category=case["category"],
        description=case.get("description", case.get("fact_note", "")),
        turns=turn_results,
        keyword_hits=hits,
        keyword_total=total,
        precision=hits / total if total > 0 else 1.0,
        has_answer=bool(last.answer.strip()) and last.error is None,
        has_citations=last.citation_count > 0,
    )
    cr.pass_ = cr.precision >= 0.5 and cr.has_answer
    return cr


def run_eval() -> None:
    print(f"BuzzBot Factual Evaluation — {len(TEST_CASES)} test cases")
    print("=" * 70)

    results: list[CaseResult] = []
    with httpx.Client(timeout=90) as client:
        for case in TEST_CASES:
            cr = run_case(client, case)
            results.append(cr)
            status = "PASS" if cr.pass_ else "FAIL"
            last_turn = cr.turns[-1]
            print(
                f"  [{cr.id}] {status} | kw={cr.keyword_hits}/{cr.keyword_total} "
                f"conf={last_turn.confidence:.2f} cit={last_turn.citation_count} "
                f"lat={last_turn.latency_s}s | {cr.description}"
            )
            if not cr.pass_:
                snippet = (last_turn.answer or last_turn.error or "")[:120]
                print(f"         rewrite: {last_turn.rewritten_query}")
                print(f"         answer:  {snippet}...")

    # Aggregate metrics
    print("\n" + "=" * 70)
    print("AGGREGATE METRICS")
    print("=" * 70)

    total = len(results)
    passed = sum(1 for r in results if r.pass_)
    answered = sum(1 for r in results if r.has_answer)
    cited = sum(1 for r in results if r.has_citations)
    avg_precision = sum(r.precision for r in results) / total if total else 0
    avg_confidence = 0
    latencies = []
    for r in results:
        for t in r.turns:
            if t.confidence:
                avg_confidence += t.confidence
            latencies.append(t.latency_s)
    n_turns = sum(len(r.turns) for r in results)
    avg_confidence = avg_confidence / n_turns if n_turns else 0

    # Per-category breakdown
    categories = sorted(set(r.category for r in results))
    cat_stats: dict[str, dict] = {}
    for cat in categories:
        cat_results = [r for r in results if r.category == cat]
        cat_pass = sum(1 for r in cat_results if r.pass_)
        cat_stats[cat] = {
            "total": len(cat_results),
            "passed": cat_pass,
            "rate": cat_pass / len(cat_results) if cat_results else 0,
        }

    print(f"Overall pass rate:    {passed}/{total} ({passed / total * 100:.0f}%)")
    print(f"Answer rate:          {answered}/{total} ({answered / total * 100:.0f}%)")
    print(f"Citation rate:        {cited}/{total} ({cited / total * 100:.0f}%)")
    print(f"Avg keyword recall:   {avg_precision:.2f}")
    print(f"Avg confidence:       {avg_confidence:.2f}")
    print(f"Avg latency:          {sum(latencies) / len(latencies):.2f}s")
    print()
    for cat, stats in cat_stats.items():
        print(f"  {cat:20s} {stats['passed']}/{stats['total']} ({stats['rate'] * 100:.0f}%)")

    # Write detailed results
    out = {
        "summary": {
            "total": total,
            "passed": passed,
            "pass_rate": round(passed / total, 3),
            "answer_rate": round(answered / total, 3),
            "citation_rate": round(cited / total, 3),
            "avg_keyword_recall": round(avg_precision, 3),
            "avg_confidence": round(avg_confidence, 3),
            "avg_latency_s": round(sum(latencies) / len(latencies), 2),
            "category_breakdown": cat_stats,
        },
        "cases": [],
    }
    for r in results:
        case_dict = {
            "id": r.id,
            "category": r.category,
            "description": r.description,
            "pass": r.pass_,
            "keyword_hits": r.keyword_hits,
            "keyword_total": r.keyword_total,
            "precision": round(r.precision, 3),
            "turns": [
                {
                    "query": t.query,
                    "answer_snippet": t.answer[:300] if t.answer else None,
                    "confidence": t.confidence,
                    "intent": t.intent,
                    "rewritten_query": t.rewritten_query,
                    "citation_count": t.citation_count,
                    "latency_s": t.latency_s,
                    "error": t.error,
                }
                for t in r.turns
            ],
        }
        out["cases"].append(case_dict)

    out_path = Path(__file__).parent / "factual_eval_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nDetailed results: {out_path}")


if __name__ == "__main__":
    run_eval()
