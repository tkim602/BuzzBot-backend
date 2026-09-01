from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.core.config import settings
from app.db.models import Chunk, Document, Embedding, Source
from app.db.session import AsyncSessionLocal
from app.graph.understanding import understand_query
from app.graph.workflow import _policy_source_types
from app.rag.retrieval import (
    RetrievedChunk,
    fts_search,
    get_text_embeddings,
    hybrid_retrieve,
    vector_search,
)
from app.retrieval import (
    CourseDetailsQuery,
    PolicyQuery,
    RegistrationCalendarQuery,
    lookup_course_details,
    lookup_registration_calendar,
    search_policy_docs,
)
from eval.quality.evidence import GoldEvidence, evidence_rank, load_gold_evidence
from eval.quality.metrics import (
    CaseResult,
    RankedItem,
    first_gold_rank,
    first_source_rank,
    first_vertical_rank,
    grouped_summary,
    normalize_url,
    summarize,
    summarize_evidence,
)
from eval.quality.schema import GoldCase, load_cases, load_manifest_cases
from ingestion.documents.registry import load_document_sources

DEFAULT_DATASET = Path(__file__).parent / "data"
DEFAULT_REPORT_DIR = Path(__file__).parent / "reports"
MODES = ("production", "raw", "vector", "fts")
_SOURCE_VERTICAL = {source.name: source.vertical for source in load_document_sources()}


def _ranked_from_chunks(chunks: list[RetrievedChunk]) -> list[RankedItem]:
    rows: list[RankedItem] = []
    for chunk in chunks:
        metadata = chunk.metadata_json or {}
        rows.append(
            RankedItem(
                url=chunk.url,
                source_name=chunk.source_name,
                vertical=(
                    str(metadata.get("vertical"))
                    if metadata.get("vertical")
                    else _SOURCE_VERTICAL.get(chunk.source_name or "")
                ),
                method=chunk.method,
                text=chunk.chunk_text,
            )
        )
    return rows


def _ranked_from_documents(documents: list[Any]) -> list[RankedItem]:
    return [
        RankedItem(
            url=document.canonical_url,
            source_name=document.source_name,
            vertical=document.vertical or _SOURCE_VERTICAL.get(document.source_name),
            method=document.retrieval_method,
            text=document.text,
        )
        for document in documents
    ]


async def _production_retrieve(session, case: GoldCase, embedding: list[float], top_k: int):
    state = understand_query(case.question)
    intent = state["intent"]
    if intent == "course_schedule":
        return [], {"intent": intent, "excluded_reason": "structured_course_schedule"}
    if intent == "course_details":
        docs = await lookup_course_details(
            session,
            CourseDetailsQuery(str(state["subject"]), str(state["course_number"]), top_k=top_k),
            embedding,
        )
    elif intent == "registration_calendar":
        docs = await lookup_registration_calendar(
            session,
            RegistrationCalendarQuery(case.question, top_k=top_k),
            embedding,
        )
    else:
        docs = await search_policy_docs(
            session,
            PolicyQuery(
                case.question,
                source_types=_policy_source_types(case.question),
                top_k=top_k,
            ),
            embedding,
        )
    return _ranked_from_documents(docs), {"intent": intent}


async def _retrieve_mode(session, mode: str, case: GoldCase, embedding: list[float], top_k: int):
    if mode == "production":
        return await _production_retrieve(session, case, embedding, top_k)
    if mode == "raw":
        chunks = await hybrid_retrieve(
            session,
            case.question,
            embedding,
            top_k=top_k,
            source_filter=None,
            similarity_threshold=settings.rag_similarity_threshold,
            force_fts=False,
        )
    elif mode == "vector":
        chunks = await vector_search(
            session,
            embedding,
            top_k=top_k,
            source_filter=None,
            similarity_threshold=settings.rag_similarity_threshold,
        )
    elif mode == "fts":
        chunks = await fts_search(
            session,
            case.question,
            top_k=top_k,
            source_filter=None,
            match_any=False,
        )
    else:
        raise ValueError(f"unknown mode: {mode}")
    return _ranked_from_chunks(chunks), {}


async def audit_corpus(session, cases: list[GoldCase]) -> dict[str, object]:
    normalized_gold = {normalize_url(url) for case in cases for url in case.gold_urls}
    stmt = (
        select(
            Document.canonical_url,
            Source.name,
            Document.metadata_json,
            func.count(Chunk.chunk_id).label("chunk_count"),
            func.count(Embedding.chunk_id).label("embedding_count"),
        )
        .join(Source, Source.id == Document.source_id)
        .outerjoin(Chunk, Chunk.doc_id == Document.doc_id)
        .outerjoin(Embedding, Embedding.chunk_id == Chunk.chunk_id)
        .group_by(Document.doc_id, Source.name)
    )
    rows = (await session.execute(stmt)).all()
    by_url = {normalize_url(row.canonical_url): row for row in rows}

    missing_urls = sorted(url for url in normalized_gold if url not in by_url)
    present_urls = normalized_gold - set(missing_urls)
    missing_embedding_urls = sorted(
        url
        for url in present_urls
        if int(by_url[url].chunk_count or 0) == 0
        or int(by_url[url].embedding_count or 0) < int(by_url[url].chunk_count or 0)
    )

    case_present = 0
    fact_presence: dict[str, bool] = {}
    metadata_mismatches: list[dict[str, object]] = []
    for case in cases:
        matched = [by_url.get(normalize_url(url)) for url in case.gold_urls]
        matched = [row for row in matched if row is not None]
        present = bool(matched)
        case_present += int(present)
        fact_presence[case.variant_group] = fact_presence.get(case.variant_group, False) or present
        if present:
            row = matched[0]
            metadata = row.metadata_json or {}
            vertical = metadata.get("vertical")
            if row.name not in case.gold_sources or (vertical and vertical != case.gold_vertical):
                metadata_mismatches.append(
                    {
                        "case_id": case.id,
                        "url": row.canonical_url,
                        "db_source": row.name,
                        "gold_sources": list(case.gold_sources),
                        "db_vertical": vertical,
                        "gold_vertical": case.gold_vertical,
                    }
                )

    return {
        "gold_urls": len(normalized_gold),
        "present_gold_urls": len(present_urls),
        "missing_gold_urls": missing_urls,
        "missing_or_incomplete_embedding_urls": missing_embedding_urls,
        "document_coverage": case_present / len(cases),
        "gold_fact_coverage": sum(fact_presence.values()) / len(fact_presence),
        "metadata_consistency": 1.0 - (len(metadata_mismatches) / len(cases)),
        "metadata_mismatches": metadata_mismatches,
    }


def _diagnose(results_by_mode: dict[str, list[CaseResult]]) -> None:
    indexed = {
        mode: {result.case.id: result for result in results}
        for mode, results in results_by_mode.items()
    }
    if "production" not in indexed:
        return
    for case_id, prod in indexed["production"].items():
        tags: list[str] = []
        raw = indexed.get("raw", {}).get(case_id)
        vector = indexed.get("vector", {}).get(case_id)
        fts = indexed.get("fts", {}).get(case_id)
        if prod.returned == 0:
            tags.append("EMPTY_RETRIEVAL")
        if not prod.hit_at(5):
            tags.append("RANK_GT_5" if prod.rank else "GOLD_NOT_RETURNED")
            tags.append("PRODUCTION_MISS")
        if raw and raw.hit_at(5) and not prod.hit_at(5):
            tags.append("RAW_RECOVERS")
        if vector and vector.hit_at(5) and raw and not raw.hit_at(5):
            tags.append("VECTOR_ONLY_RECOVERS")
        if fts and fts.hit_at(5) and raw and not raw.hit_at(5):
            tags.append("FTS_ONLY_RECOVERS")
        if raw and raw.hit_at(5) and not prod.hit_at(5):
            tags.append("PRODUCTION_FILTER_OR_ROUTING_LOSS")
        if all(row is not None and not row.hit_at(5) for row in (raw, vector, fts)):
            tags.append("ALL_ABLATIONS_MISS")
            if prod.hit_at(5):
                tags.append("PRODUCTION_RECOVERS_ABLATIONS")
        object.__setattr__(prod, "failure_tags", tuple(tags))


def _production_lift_at_5(summaries: dict[str, dict[str, object]]) -> float:
    return float(summaries["production"]["hit_at_5"]) - float(summaries["raw"]["hit_at_5"])


def _coverage_summary(
    corpus_audit: dict[str, object], evidence: dict[str, GoldEvidence]
) -> dict[str, float | None]:
    return {
        "document_coverage": float(corpus_audit["document_coverage"]),
        "evidence_coverage": (
            sum(row.span is not None for row in evidence.values()) / len(evidence)
            if evidence
            else None
        ),
    }


def _evaluation_cases(dataset: Path, manifest: Path | None) -> list[GoldCase]:
    return load_manifest_cases(manifest) if manifest else load_cases(dataset)


async def run(
    dataset: Path,
    report_dir: Path,
    top_k: int = 10,
    manifest: Path | None = None,
    evidence_file: Path | None = None,
) -> dict[str, object]:
    cases = _evaluation_cases(dataset, manifest)
    gold_evidence = load_gold_evidence(evidence_file, cases) if evidence_file else {}
    report_dir.mkdir(parents=True, exist_ok=True)

    async with AsyncSessionLocal() as session:
        corpus_audit = await audit_corpus(session, cases)

        embedding_t0 = time.perf_counter()
        embeddings = await get_text_embeddings([case.question for case in cases])
        embedding_batch_ms = (time.perf_counter() - embedding_t0) * 1000
        if len(embeddings) != len(cases):
            raise RuntimeError("embedding result count does not match dataset")

        results_by_mode: dict[str, list[CaseResult]] = {}
        metadata_by_case: dict[str, dict[str, object]] = {}
        for mode in MODES:
            mode_results: list[CaseResult] = []
            for case, embedding in zip(cases, embeddings, strict=True):
                t0 = time.perf_counter()
                items, metadata = await _retrieve_mode(session, mode, case, embedding, top_k)
                latency_ms = (time.perf_counter() - t0) * 1000
                if mode == "production":
                    metadata_by_case[case.id] = metadata
                mode_results.append(
                    CaseResult(
                        case=case,
                        mode=mode,
                        rank=first_gold_rank(case, items),
                        source_rank=first_source_rank(case, items),
                        vertical_rank=first_vertical_rank(case, items),
                        returned=len(items),
                        latency_ms=latency_ms,
                        items=tuple(items),
                        evidence_rank=(
                            evidence_rank(gold_evidence[case.variant_group], items)
                            if gold_evidence
                            else None
                        ),
                    )
                )
            results_by_mode[mode] = mode_results

    _diagnose(results_by_mode)

    summaries = {mode: summarize(results) for mode, results in results_by_mode.items()}
    production = results_by_mode["production"]
    production_lift = _production_lift_at_5(summaries)

    report = {
        "benchmark": "buzzbot_gt_public_gold_1000",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(dataset),
        "manifest": str(manifest) if manifest else None,
        "cases": len(cases),
        "facts": len({case.variant_group for case in cases}),
        "top_k": top_k,
        "embedding_batch_ms": embedding_batch_ms,
        "corpus_audit": corpus_audit,
        "coverage": _coverage_summary(corpus_audit, gold_evidence),
        "modes": summaries,
        "ablation": {
            "vector": summaries["vector"],
            "fts": summaries["fts"],
            "hybrid": summaries["raw"],
        },
        "production_lift_at_5": production_lift,
        "breakdowns": {
            "vertical": grouped_summary(production, "gold_vertical"),
            "source": _group_by_source(production),
            "difficulty": grouped_summary(production, "difficulty"),
            "style": grouped_summary(production, "style"),
            "question_type": grouped_summary(production, "question_type"),
            "time_sensitive": grouped_summary(production, "time_sensitive"),
        },
        "failure_counts": dict(Counter(tag for row in production for tag in row.failure_tags)),
    }
    if gold_evidence:
        report["evidence"] = summarize_evidence(production)

    summary_path = report_dir / "latest_summary.json"
    cases_path = report_dir / "latest_cases.jsonl"
    failures_path = report_dir / "latest_failures.jsonl"
    markdown_path = report_dir / "latest_summary.md"
    summary_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    with cases_path.open("w", encoding="utf-8") as handle:
        for mode, results in results_by_mode.items():
            for result in results:
                payload = _case_payload(result)
                if mode == "production":
                    payload["production_metadata"] = metadata_by_case.get(result.case.id, {})
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    with failures_path.open("w", encoding="utf-8") as handle:
        for result in production:
            if result.failure_tags:
                handle.write(json.dumps(_case_payload(result), ensure_ascii=False) + "\n")

    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _group_by_source(results: list[CaseResult]) -> dict[str, dict[str, object]]:
    buckets: dict[str, list[CaseResult]] = {}
    for result in results:
        for source in result.case.gold_sources:
            buckets.setdefault(source, []).append(result)
    return {source: summarize(rows) for source, rows in sorted(buckets.items())}


def _case_payload(result: CaseResult) -> dict[str, object]:
    return {
        "case_id": result.case.id,
        "variant_group": result.case.variant_group,
        "question": result.case.question,
        "mode": result.mode,
        "gold_urls": list(result.case.gold_urls),
        "gold_sources": list(result.case.gold_sources),
        "gold_vertical": result.case.gold_vertical,
        "difficulty": result.case.difficulty,
        "style": result.case.style,
        "rank": result.rank,
        "source_rank": result.source_rank,
        "vertical_rank": result.vertical_rank,
        "returned": result.returned,
        "latency_ms": result.latency_ms,
        "failure_tags": list(result.failure_tags),
        "evidence_rank": result.evidence_rank,
        "retrieved": [asdict(item) for item in result.items],
    }


def _render_markdown(report: dict[str, object]) -> str:
    lines = [
        "# BuzzBot deterministic retrieval quality report",
        "",
        f"- Questions: {report['cases']}",
        f"- Underlying facts: {report['facts']}",
        f"- Gold document coverage: {report['coverage']['document_coverage']:.2%}",
        f"- Gold evidence coverage: {_percent_or_na(report['coverage']['evidence_coverage'])}",
        "",
        "## Headline metrics",
        "",
        "| Mode | Hit@1 | Hit@3 | Hit@5 | MRR@5 | p95 ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mode in MODES:
        summary = report["modes"][mode]
        lines.append(
            f"| {mode} | {summary['hit_at_1']:.2%} | {summary['hit_at_3']:.2%} | "
            f"{summary['hit_at_5']:.2%} | {summary['mrr_at_5']:.3f} | "
            f"{summary['latency_ms']['p95']:.1f} |"
        )
    lines.extend(
        [
            "",
            f"Production lift over raw at 5: {report['production_lift_at_5']:.2%}",
            "",
            "## Production robustness",
            "",
            f"- Fact macro Hit@5: {report['modes']['production']['fact_macro_hit_at_5']:.2%}",
            f"- All variants Hit@5: {report['modes']['production']['all_variants_hit_at_5']:.2%}",
            f"- Empty retrieval rate: {report['modes']['production']['empty_retrieval_rate']:.2%}",
            "",
            "## Failure tags",
            "",
        ]
    )
    for tag, count in sorted(report["failure_counts"].items()):
        lines.append(f"- {tag}: {count}")
    return "\n".join(lines) + "\n"


def _percent_or_na(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.2%}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run BuzzBot deterministic retrieval quality eval")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--evidence-file", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(
        run(
            args.dataset,
            args.report_dir,
            top_k=args.top_k,
            manifest=args.manifest,
            evidence_file=args.evidence_file,
        )
    )
    production = report["modes"]["production"]
    raw = report["modes"]["raw"]
    print("\n=== BuzzBot deterministic retrieval quality ===")
    print(f"questions={report['cases']} facts={report['facts']}")
    print(f"document_coverage={report['coverage']['document_coverage']:.2%}")
    print(f"evidence_coverage={_percent_or_na(report['coverage']['evidence_coverage'])}")
    print(
        f"production hit@1={production['hit_at_1']:.2%} hit@3={production['hit_at_3']:.2%} "
        f"hit@5={production['hit_at_5']:.2%} mrr@5={production['mrr_at_5']:.3f}"
    )
    print(f"raw hit@5={raw['hit_at_5']:.2%} production_lift@5={report['production_lift_at_5']:.2%}")
    print(f"report={args.report_dir / 'latest_summary.md'}")


if __name__ == "__main__":
    main()
