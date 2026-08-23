from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import mean
from urllib.parse import urlsplit, urlunsplit

from eval.quality.schema import GoldCase


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


@dataclass(frozen=True)
class RankedItem:
    url: str | None
    source_name: str | None
    vertical: str | None
    method: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class CaseResult:
    case: GoldCase
    mode: str
    rank: int | None
    source_rank: int | None
    vertical_rank: int | None
    returned: int
    latency_ms: float
    items: tuple[RankedItem, ...]
    failure_tags: tuple[str, ...] = ()
    evidence_rank: int | None = None

    def hit_at(self, k: int) -> bool:
        return self.rank is not None and self.rank <= k

    def source_hit_at(self, k: int) -> bool:
        return self.source_rank is not None and self.source_rank <= k

    def vertical_hit_at(self, k: int) -> bool:
        return self.vertical_rank is not None and self.vertical_rank <= k


def first_gold_rank(case: GoldCase, items: Iterable[RankedItem]) -> int | None:
    gold = {normalize_url(url) for url in case.gold_urls}
    for rank, item in enumerate(items, start=1):
        if item.url and normalize_url(item.url) in gold:
            return rank
    return None


def first_source_rank(case: GoldCase, items: Iterable[RankedItem]) -> int | None:
    gold = set(case.gold_sources)
    for rank, item in enumerate(items, start=1):
        if item.source_name in gold:
            return rank
    return None


def first_vertical_rank(case: GoldCase, items: Iterable[RankedItem]) -> int | None:
    for rank, item in enumerate(items, start=1):
        if item.vertical == case.gold_vertical:
            return rank
    return None


def reciprocal_rank(rank: int | None, k: int = 5) -> float:
    return 1.0 / rank if rank is not None and rank <= k else 0.0


def summarize(results: list[CaseResult]) -> dict[str, object]:
    if not results:
        return {"cases": 0}
    total = len(results)
    groups: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        groups[result.case.variant_group].append(result)

    fact_hit_rates = [mean(float(row.hit_at(5)) for row in rows) for rows in groups.values()]
    all_variant_hits = [all(row.hit_at(5) for row in rows) for rows in groups.values()]

    latencies = sorted(result.latency_ms for result in results)

    def percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        idx = min(len(values) - 1, max(0, round((len(values) - 1) * q)))
        return values[idx]

    return {
        "cases": total,
        "facts": len(groups),
        "hit_at_1": mean(float(r.hit_at(1)) for r in results),
        "hit_at_3": mean(float(r.hit_at(3)) for r in results),
        "hit_at_5": mean(float(r.hit_at(5)) for r in results),
        "mrr_at_5": mean(reciprocal_rank(r.rank, 5) for r in results),
        "source_hit_at_1": mean(float(r.source_hit_at(1)) for r in results),
        "source_hit_at_5": mean(float(r.source_hit_at(5)) for r in results),
        "vertical_hit_at_1": mean(float(r.vertical_hit_at(1)) for r in results),
        "empty_retrieval_rate": mean(float(r.returned == 0) for r in results),
        "fact_macro_hit_at_5": mean(fact_hit_rates),
        "all_variants_hit_at_5": mean(float(hit) for hit in all_variant_hits),
        "latency_ms": {
            "mean": mean(latencies),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
        },
    }


def summarize_evidence(results: list[CaseResult]) -> dict[str, float]:
    return {
        "evidence_hit_at_1": mean(float(r.evidence_rank == 1) for r in results),
        "evidence_hit_at_3": mean(
            float(r.evidence_rank is not None and r.evidence_rank <= 3) for r in results
        ),
        "evidence_hit_at_5": mean(
            float(r.evidence_rank is not None and r.evidence_rank <= 5) for r in results
        ),
        "evidence_mrr_at_5": mean(reciprocal_rank(r.evidence_rank, 5) for r in results),
    }


def grouped_summary(results: list[CaseResult], field: str) -> dict[str, dict[str, object]]:
    groups: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        value = getattr(result.case, field)
        groups[str(value)].append(result)
    return {name: summarize(rows) for name, rows in sorted(groups.items())}
