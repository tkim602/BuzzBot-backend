"""Budget-free deterministic evaluation for the query-understanding gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.graph.understanding import understand_query

DEFAULT_GOLDEN = Path(__file__).with_name("agentic_rag_golden.jsonl")


def load_cases(path: Path = DEFAULT_GOLDEN) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, object]:
    details: list[dict[str, object]] = []
    route_hits = 0
    field_hits = 0
    field_total = 0
    for case in cases:
        result = understand_query(case["query"], case.get("user_term"))
        route_match = result["intent"] == case["expected_intent"]
        route_hits += int(route_match)
        mismatches: dict[str, dict[str, object]] = {}
        for field, expected in case.get("expected_fields", {}).items():
            field_total += 1
            actual = result.get(field)
            if actual == expected:
                field_hits += 1
            else:
                mismatches[field] = {"expected": expected, "actual": actual}
        details.append(
            {
                "id": case["id"],
                "route_match": route_match,
                "field_mismatches": mismatches,
            }
        )
    case_count = len(cases)
    return {
        "mode": "offline_no_api",
        "cases": case_count,
        "routing_accuracy": route_hits / case_count if case_count else 0.0,
        "required_field_accuracy": field_hits / field_total if field_total else 0.0,
        "passed": route_hits == case_count and field_hits == field_total,
        "details": details,
    }


def main() -> int:
    report = evaluate_cases(load_cases())
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
