from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.usage import get_usage
from app.graph.understanding import understand_query
from app.rag.retrieval import get_text_embeddings
from app.retrieval import RegistrationCalendarQuery, lookup_registration_calendar
from app.retrieval.documents import DocumentEvidence
from db.session import AsyncSessionLocal


@dataclass(frozen=True)
class CalendarCase:
    case_id: str
    question: str
    event_id: int
    gold_span: str
    gold_url: str
    gold_source: str


def load_cases(path: Path) -> list[CalendarCase]:
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    cases = [
        CalendarCase(
            case_id=str(item["id"]),
            question=str(item["question"]),
            event_id=int(item["event_id"]),
            gold_span=str(item["gold_span"]),
            gold_url=str(item["gold_url"]),
            gold_source=str(item["gold_source"]),
        )
        for item in items
    ]
    if len(cases) != 20 or len({case.case_id for case in cases}) != 20:
        raise ValueError("academic-calendar-20-v1 must contain 20 unique cases")
    return cases


def target_event_rank(event_id: int, evidence: list[DocumentEvidence]) -> int | None:
    marker = re.compile(rf"\bEvent {event_id}\b")
    return next(
        (rank for rank, item in enumerate(evidence, start=1) if marker.search(item.text)), None
    )


async def run(path: Path) -> dict[str, object]:
    cases = load_cases(path)
    started = time.perf_counter()
    before = get_usage()
    embeddings = await get_text_embeddings([case.question for case in cases])
    route_failures = [
        case.case_id
        for case in cases
        if understand_query(case.question)["intent"] != "registration_calendar"
    ]
    rows = []
    async with AsyncSessionLocal() as session:
        for case, embedding in zip(cases, embeddings, strict=True):
            evidence = await lookup_registration_calendar(
                session, RegistrationCalendarQuery(case.question, top_k=5), embedding
            )
            rows.append(
                {"case_id": case.case_id, "rank": target_event_rank(case.event_id, evidence)}
            )
    after = get_usage()
    ranks = [row["rank"] for row in rows]
    return {
        "cases": len(rows),
        "route_accuracy": (len(rows) - len(route_failures)) / len(rows),
        "event_hit_at_1": sum(rank == 1 for rank in ranks) / len(rows),
        "event_hit_at_5": sum(rank is not None for rank in ranks) / len(rows),
        "event_mrr_at_5": sum(1 / rank for rank in ranks if rank) / len(rows),
        "route_failures": route_failures,
        "retrieval_misses": [row["case_id"] for row in rows if row["rank"] is None],
        "cost_usd": float(after.get("total_cost", 0)) - float(before.get("total_cost", 0)),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the frozen Academic Calendar suite")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    summary = asyncio.run(run(parser.parse_args().manifest))
    print(json.dumps(summary, separators=(",", ":")))
    return int(bool(summary["route_failures"] or summary["retrieval_misses"]))


if __name__ == "__main__":
    raise SystemExit(main())
