"""Compare baseline vs improved retrieval behavior for Phase-1 fixes."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.rag.query_rewrite import rewrite_query
from app.rag.retrieval import get_query_embedding, hybrid_retrieve
from app.rag.router import classify_query
from db.session import AsyncSessionLocal


@dataclass
class Case:
    query: str
    must_have: list[str]
    expected_sources: list[str]
    history: list[dict] | None = None


CASES = [
    Case(
        query="When is the registration deadline?",
        must_have=["registration", "deadline"],
        expected_sources=["gt-registrar"],
    ),
    Case(
        query="When is the registration deadline for CS 4400 Spring 2025?",
        must_have=["registration", "spring 2025", "cs 4400"],
        expected_sources=["gt-registrar", "gt-scheduler", "gt-catalog"],
    ),
    Case(
        query="Is it offered in Spring 2025?",
        must_have=["cs 4400", "spring 2025"],
        expected_sources=["gt-scheduler"],
        history=[
            {"role": "user", "content": "Tell me about CS 4400."},
            {"role": "assistant", "content": "CS 4400 is Introduction to Database Systems."},
        ],
    ),
    Case(
        query="What is the add/drop deadline this semester?",
        must_have=["add", "drop"],
        expected_sources=["gt-registrar"],
    ),
    Case(
        query="When is commencement this year?",
        must_have=["commencement"],
        expected_sources=["gt-registrar"],
    ),
]


def _contains_all(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return all(n.lower() in lowered for n in needles)


def _evaluate_chunks(chunks, case: Case, top_k: int = 5) -> tuple[bool, bool]:
    top = chunks[:top_k]
    blob = "\n".join((c.title or "") + "\n" + c.chunk_text for c in top)
    has_all = _contains_all(blob, case.must_have)
    source_hit = any((c.source_name in case.expected_sources) for c in top)
    return has_all, source_hit


async def _run_baseline(case: Case, session) -> dict:
    route = classify_query(case.query)
    emb = await get_query_embedding(case.query)
    chunks = await hybrid_retrieve(
        session=session,
        query=case.query,
        query_embedding=emb,
        top_k=5,
        source_filter=route.source_filter,
        similarity_threshold=settings.rag_similarity_threshold,
        force_fts=False,
    )
    coverage, source_hit = _evaluate_chunks(chunks, case)
    return {
        "route_source": route.source_filter,
        "retrieved": len(chunks),
        "coverage_hit": coverage,
        "source_hit": source_hit,
        "top_sources": [c.source_name for c in chunks[:5]],
    }


async def _run_improved(case: Case, session) -> dict:
    rewrite = await rewrite_query(case.query, history=case.history)
    route = classify_query(rewrite.rewritten_query)

    source_filter: str | list[str] | None = route.source_filter
    if route.intent == "registrar_calendar" and rewrite.detected_course_code:
        source_filter = ["gt-registrar", "gt-scheduler", "gt-catalog"]

    emb = await get_query_embedding(rewrite.rewritten_query)
    chunks = await hybrid_retrieve(
        session=session,
        query=rewrite.rewritten_query,
        query_embedding=emb,
        top_k=5,
        source_filter=source_filter,
        similarity_threshold=settings.rag_similarity_threshold,
        force_fts=settings.rag_force_fts_for_date_sensitive and rewrite.date_sensitive,
    )
    coverage, source_hit = _evaluate_chunks(chunks, case)
    return {
        "route_source": route.source_filter,
        "effective_source_filter": source_filter,
        "rewritten_query": rewrite.rewritten_query,
        "date_sensitive": rewrite.date_sensitive,
        "current_term": rewrite.current_term,
        "retrieved": len(chunks),
        "coverage_hit": coverage,
        "source_hit": source_hit,
        "top_sources": [c.source_name for c in chunks[:5]],
    }


async def run() -> dict:
    rows: list[dict] = []

    baseline_coverage = 0
    improved_coverage = 0
    baseline_source = 0
    improved_source = 0

    async with AsyncSessionLocal() as session:
        for case in CASES:
            before = await _run_baseline(case, session)
            after = await _run_improved(case, session)

            baseline_coverage += int(before["coverage_hit"])
            improved_coverage += int(after["coverage_hit"])
            baseline_source += int(before["source_hit"])
            improved_source += int(after["source_hit"])

            rows.append(
                {
                    "query": case.query,
                    "must_have": case.must_have,
                    "baseline": before,
                    "improved": after,
                }
            )

    total = len(CASES)
    return {
        "cases": total,
        "coverage_at_5": {
            "baseline": round(baseline_coverage / total, 3),
            "improved": round(improved_coverage / total, 3),
            "delta": round((improved_coverage - baseline_coverage) / total, 3),
        },
        "source_hit_at_5": {
            "baseline": round(baseline_source / total, 3),
            "improved": round(improved_source / total, 3),
            "delta": round((improved_source - baseline_source) / total, 3),
        },
        "details": rows,
    }


if __name__ == "__main__":
    result = asyncio.run(run())
    out = Path(__file__).parent / "pipeline_phase1_eval_results.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nWrote: {out}")
