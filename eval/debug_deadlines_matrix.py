"""Deterministic debug matrix for deadline/admissions retrieval quality."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env")

from app.core.config import settings
from app.rag.query_rewrite import rewrite_query
from app.rag.retrieval import get_query_embedding, hybrid_retrieve
from app.rag.router import classify_query
from db.session import AsyncSessionLocal

ARTIFACT_PATH = Path(__file__).resolve().parent.parent / "artifacts" / "debug_deadlines_matrix.json"

CASES = [
    {
        "id": "registrar_last_day_register_add",
        "query": "When is the last day to register or add courses for Spring 2026?",
        "history": [],
    },
    {
        "id": "registrar_registration_deadline",
        "query": "When is the registration deadline?",
        "history": [],
    },
    {
        "id": "admissions_omscs_deadline",
        "query": "application deadline for OMSCS",
        "history": [],
    },
    {
        "id": "admissions_mscs_deadline",
        "query": "application deadline for MSCS",
        "history": [],
    },
    {
        "id": "followup_march_first",
        "query": "isnt it Mar 1?",
        "history": [
            {"role": "user", "content": "when is the application deadline for Fall 2026"},
            {
                "role": "assistant",
                "content": "I could not find the exact date yet.",
            },
            {"role": "user", "content": "application deadline for OMSCS"},
            {
                "role": "assistant",
                "content": "I could not find OMSCS-specific deadline info yet.",
            },
        ],
    },
]


def _chunk_preview(text: str, limit: int = 260) -> str:
    return " ".join((text or "").split())[:limit]


async def run() -> dict:
    # Keep this harness deterministic and low cost.
    original_mode = settings.rag_query_rewrite_mode
    original_hyde = settings.rag_enable_hyde
    original_rerank = settings.rag_enable_reranking
    settings.rag_query_rewrite_mode = "rule"
    settings.rag_enable_hyde = False
    settings.rag_enable_reranking = False

    rows: list[dict] = []
    try:
        async with AsyncSessionLocal() as session:
            for case in CASES:
                rewrite = await rewrite_query(case["query"], history=case["history"])
                route = classify_query(rewrite.rewritten_query)
                query_embedding = await get_query_embedding(rewrite.rewritten_query)

                chunks = await hybrid_retrieve(
                    session=session,
                    query=rewrite.rewritten_query,
                    query_embedding=query_embedding,
                    top_k=settings.rag_top_k,
                    source_filter=route.source_filter,
                    similarity_threshold=settings.rag_similarity_threshold,
                    force_fts=settings.rag_force_fts_for_date_sensitive and rewrite.date_sensitive,
                    hyde_embedding=None,
                )

                rows.append(
                    {
                        "id": case["id"],
                        "query": case["query"],
                        "rewritten_query": rewrite.rewritten_query,
                        "date_sensitive": rewrite.date_sensitive,
                        "intent": route.intent,
                        "freshness_strategy": route.freshness_strategy,
                        "source_filter": route.source_filter,
                        "retrieval_top_k": len(chunks),
                        "top_chunks": [
                            {
                                "rank": idx,
                                "source": chunk.source_name,
                                "url": chunk.url,
                                "method": chunk.method,
                                "score": round(float(chunk.score), 4),
                                "preview": _chunk_preview(chunk.chunk_text),
                            }
                            for idx, chunk in enumerate(chunks[:6], start=1)
                        ],
                    }
                )
    finally:
        settings.rag_query_rewrite_mode = original_mode
        settings.rag_enable_hyde = original_hyde
        settings.rag_enable_reranking = original_rerank

    result = {
        "meta": {
            "rewrite_mode": "rule",
            "hyde_enabled": False,
            "reranking_enabled": False,
            "cases": len(CASES),
        },
        "results": rows,
    }
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    data = asyncio.run(run())
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\nWrote: {ARTIFACT_PATH}")
