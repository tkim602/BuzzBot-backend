"""Regression checks for routing + retrieval quality."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from app.rag.retrieval import get_query_embedding, hybrid_retrieve
from app.rag.router import classify_query
from db.session import AsyncSessionLocal


@dataclass
class Case:
    query: str
    expected_source: str
    must_have: list[str]


CASES = [
    Case("Is CS 4400 offered in Spring 2025?", "gt-scheduler", ["cs 4400", "spring 2025"]),
    Case("Will CS1332 be available in Fall 2025?", "gt-scheduler", ["cs 1332", "fall 2025"]),
    Case("Tell me CS 2110 section times for Fall 2024", "gt-scheduler", ["cs 2110", "fall 2024"]),
    Case("What are the prerequisites for CS 1332?", "gt-catalog", ["cs 1332"]),
    Case("How many credit hours is CS 2110?", "gt-catalog", ["cs 2110"]),
    Case("When is the registration deadline for Fall 2026?", "gt-registrar", ["fall 2026"]),
    Case("학사일정 등록 마감일 언제야?", "gt-registrar", ["등록"]),
    Case("How do I reserve a study room in the library?", "gt-library", ["library"]),
]


def _contains_all(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return all(n.lower() in lowered for n in needles)


async def run() -> dict:
    route_ok = 0
    top_source_ok = 0
    strict_ok = 0
    rows: list[dict] = []

    async with AsyncSessionLocal() as session:
        for case in CASES:
            route = classify_query(case.query)
            emb = await get_query_embedding(case.query)
            chunks = await hybrid_retrieve(
                session=session,
                query=case.query,
                query_embedding=emb,
                top_k=8,
                source_filter=route.source_filter,
                similarity_threshold=0.3,
            )
            top_source = chunks[0].source_name if chunks else None
            blob = (
                "\n".join((c.title or "") + "\n" + c.chunk_text for c in chunks[:3])
                if chunks
                else ""
            )

            if isinstance(route.source_filter, list):
                route_match = case.expected_source in route.source_filter
            else:
                route_match = route.source_filter == case.expected_source
            source_match = top_source == case.expected_source
            strict_match = _contains_all(blob, case.must_have)

            route_ok += int(route_match)
            top_source_ok += int(source_match)
            strict_ok += int(strict_match)

            rows.append(
                {
                    "query": case.query,
                    "expected_source": case.expected_source,
                    "route_source": route.source_filter,
                    "top_source": top_source,
                    "retrieved_chunks": len(chunks),
                    "route_match": route_match,
                    "top_source_match": source_match,
                    "strict_match": strict_match,
                }
            )

    total = len(CASES)
    summary = {
        "cases": total,
        "route_match": {"count": route_ok, "rate": round(route_ok / total, 3)},
        "top_source_match": {"count": top_source_ok, "rate": round(top_source_ok / total, 3)},
        "strict_match": {"count": strict_ok, "rate": round(strict_ok / total, 3)},
        "details": rows,
    }
    return summary


if __name__ == "__main__":
    result = asyncio.run(run())
    out = Path(__file__).parent / "retrieval_regression_results.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nWrote: {out}")
