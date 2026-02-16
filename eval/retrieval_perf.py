"""Simple retrieval latency benchmark (embedding excluded)."""

from __future__ import annotations

import asyncio
import time
from statistics import mean

from dotenv import load_dotenv

load_dotenv()

from app.rag.retrieval import get_query_embedding, hybrid_retrieve
from db.session import AsyncSessionLocal

CASES = [
    ("Is CS 4400 offered in Spring 2025?", "gt-scheduler"),
    ("What are the prerequisites for CS 1332?", "gt-catalog"),
    ("When is the registration deadline for Fall 2026?", "gt-registrar"),
    ("How do I reserve a study room in the library?", "gt-library"),
]


async def run(iterations: int = 8) -> None:
    async with AsyncSessionLocal() as session:
        for query, source_filter in CASES:
            emb = await get_query_embedding(query)
            await hybrid_retrieve(session, query, emb, top_k=8, source_filter=source_filter)

            samples_ms: list[float] = []
            for _ in range(iterations):
                t0 = time.perf_counter()
                await hybrid_retrieve(session, query, emb, top_k=8, source_filter=source_filter)
                samples_ms.append((time.perf_counter() - t0) * 1000)

            samples_sorted = sorted(samples_ms)
            p95 = samples_sorted[max(0, int(len(samples_sorted) * 0.95) - 1)]
            print(
                f"{query[:55]:55} avg_ms={mean(samples_ms):.1f} "
                f"p95_ms={p95:.1f} min_ms={min(samples_ms):.1f} max_ms={max(samples_ms):.1f}"
            )


if __name__ == "__main__":
    asyncio.run(run())

