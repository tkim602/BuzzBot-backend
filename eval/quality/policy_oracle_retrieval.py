from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.db.models import Chunk, Document, Source
from app.db.session import AsyncSessionLocal
from app.rag.retrieval import (
    RetrievedChunk,
    _extract_query_hints,
    _lexical_match_score,
    _rrf_fuse_results,
    _signal_match_count,
    get_text_embeddings,
    rerank_with_cross_encoder,
    vector_search,
)
from eval.quality.evidence import GoldEvidence, evidence_rank, load_gold_evidence
from eval.quality.metrics import RankedItem, normalize_url
from eval.quality.runner import _production_retrieve
from eval.quality.schema import load_manifest_cases

DEFAULT_MANIFEST = Path("eval/quality/manifests/dev_100.json")
DEFAULT_EVIDENCE = Path("eval/quality/gold_evidence/dev_100.json")
DEFAULT_TAXONOMY = Path("eval/quality/policy_evidence_miss_diagnosis_pr11_after.json")
DEFAULT_OUTPUT = Path("eval/quality/policy_oracle_retrieval_pr12.json")
DEFAULT_CASES_OUTPUT = Path("eval/quality/policy_oracle_retrieval_pr12_cases.jsonl")
DEFAULT_MARKDOWN = Path("docs/evals/policy_oracle_retrieval_pr12.md")


def _ranked(chunks: list[RetrievedChunk]) -> list[RankedItem]:
    return [
        RankedItem(
            url=chunk.url,
            source_name=chunk.source_name,
            vertical=str((chunk.metadata_json or {}).get("vertical") or "") or None,
            method=chunk.method,
            text=chunk.chunk_text,
        )
        for chunk in chunks
    ]


def rank_document_chunks(
    query: str,
    canonical_url: str,
    vector_chunks: list[RetrievedChunk],
    document_chunks: list[RetrievedChunk],
    *,
    top_k: int = 5,
    rerank: bool = True,
) -> list[RetrievedChunk]:
    """Rank chunks from one known document with the existing retrieval signals."""
    target = normalize_url(canonical_url)
    vectors = [
        replace(chunk) for chunk in vector_chunks if normalize_url(chunk.url or "") == target
    ]
    lexical = [
        replace(chunk, score=_lexical_match_score(query, chunk), method="oracle_lexical")
        for chunk in document_chunks
        if normalize_url(chunk.url or "") == target
    ]
    lexical.sort(key=lambda chunk: chunk.score, reverse=True)
    fusion_k = max(top_k, 15)
    merged = _rrf_fuse_results(vectors, lexical, top_k=fusion_k)
    hints = _extract_query_hints(query)
    merged.sort(
        key=lambda chunk: (
            _signal_match_count(chunk, hints),
            _lexical_match_score(query, chunk),
            chunk.score,
        ),
        reverse=True,
    )
    if rerank and settings.rag_enable_reranking and len(merged) > 1:
        merged = rerank_with_cross_encoder(query, merged[:15], top_k=top_k)
    return merged[:top_k]


def summarize_evidence_ranks(
    ranks: list[int | None], latencies_ms: list[float] | None = None
) -> dict[str, Any]:
    if not ranks:
        raise ValueError("at least one evidence rank is required")
    summary: dict[str, Any] = {
        "cases": len(ranks),
        "evidence_hit_at_1": mean(rank == 1 for rank in ranks),
        "evidence_hit_at_3": mean(rank is not None and rank <= 3 for rank in ranks),
        "evidence_hit_at_5": mean(rank is not None and rank <= 5 for rank in ranks),
        "evidence_mrr_at_5": mean(
            1.0 / rank if rank is not None and rank <= 5 else 0.0 for rank in ranks
        ),
    }
    if latencies_ms is not None:
        if len(latencies_ms) != len(ranks):
            raise ValueError("latency count must match evidence rank count")
        ordered = sorted(latencies_ms)
        p95_index = min(len(ordered) - 1, round((len(ordered) - 1) * 0.95))
        summary["latency_ms"] = {
            "mean": mean(ordered),
            "p95": ordered[p95_index],
        }
    return summary


def group_unresolved_categories(
    rows: list[dict[str, Any]], categories: dict[str, str]
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[int | None]] = defaultdict(list)
    for row in rows:
        category = categories.get(str(row["case_id"]))
        if category and category != "RESOLVED_SINCE_FREEZE":
            grouped[category].append(row.get("oracle_evidence_rank"))
    return {
        category: summarize_evidence_ranks(ranks) for category, ranks in sorted(grouped.items())
    }


def architectural_decision(oracle_evidence_hit_at_5: float) -> str:
    if oracle_evidence_hit_at_5 >= 0.90:
        return "HIERARCHICAL_RETRIEVAL_SUPPORTED"
    return "REPRESENTATION_OR_WITHIN_DOCUMENT_RANKING"


def _load_categories(path: Path, case_ids: set[str]) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 30:
        raise ValueError(f"{path}: expected the 30-case PR11 taxonomy")
    categories = {str(row["case_id"]): str(row["root_cause"]) for row in rows}
    if len(categories) != len(rows) or not set(categories) <= case_ids:
        raise ValueError(f"{path}: invalid or unknown case ids")
    return categories


def _chunk_from_model(chunk: Chunk, source_name: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(chunk.chunk_id),
        url=chunk.url,
        title=chunk.title,
        headings=chunk.headings,
        chunk_text=chunk.chunk_text,
        score=0.0,
        source_name=source_name,
        fetched_at=chunk.fetched_at.isoformat() if chunk.fetched_at else None,
        metadata_json=chunk.metadata_json,
        method="oracle_document",
    )


async def _load_gold_documents(
    session, gold_by_group: dict[str, GoldEvidence]
) -> dict[str, tuple[Document, str, list[RetrievedChunk]]]:
    targets = {normalize_url(gold.url) for gold in gold_by_group.values()}
    document_rows = (
        await session.execute(
            select(Document, Source.name).join(Source, Source.id == Document.source_id)
        )
    ).all()
    selected = {
        normalize_url(document.canonical_url): (document, source_name)
        for document, source_name in document_rows
        if normalize_url(document.canonical_url) in targets
    }
    chunks_by_doc: dict[Any, list[Chunk]] = defaultdict(list)
    doc_ids = [document.doc_id for document, _ in selected.values()]
    if doc_ids:
        chunks = (await session.scalars(select(Chunk).where(Chunk.doc_id.in_(doc_ids)))).all()
        for chunk in chunks:
            chunks_by_doc[chunk.doc_id].append(chunk)
    return {
        url: (
            document,
            source_name,
            [_chunk_from_model(chunk, source_name) for chunk in chunks_by_doc[document.doc_id]],
        )
        for url, (document, source_name) in selected.items()
    }


async def run(
    manifest_path: Path = DEFAULT_MANIFEST,
    evidence_path: Path = DEFAULT_EVIDENCE,
    taxonomy_path: Path = DEFAULT_TAXONOMY,
    *,
    top_k: int = 5,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases = load_manifest_cases(manifest_path)
    if len(cases) != 100:
        raise ValueError(f"{manifest_path}: expected 100 Policy dev cases")
    gold_by_group = load_gold_evidence(evidence_path, cases)
    categories = _load_categories(taxonomy_path, {case.id for case in cases})

    embedding_started = time.perf_counter()
    embeddings = await get_text_embeddings([case.question for case in cases])
    embedding_batch_ms = (time.perf_counter() - embedding_started) * 1000
    if len(embeddings) != len(cases):
        raise RuntimeError("embedding result count does not match manifest")

    rows: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as session:
        documents = await _load_gold_documents(session, gold_by_group)
        for case, embedding in zip(cases, embeddings, strict=True):
            gold = gold_by_group[case.variant_group]

            started = time.perf_counter()
            global_items, metadata = await _production_retrieve(session, case, embedding, top_k)
            global_latency_ms = (time.perf_counter() - started) * 1000
            global_rank = evidence_rank(gold, global_items)

            started = time.perf_counter()
            oracle_chunks: list[RetrievedChunk] = []
            document_data = documents.get(normalize_url(gold.url))
            if document_data:
                document, source_name, document_chunks = document_data
                vector_chunks = await vector_search(
                    session,
                    embedding,
                    top_k=max(1, len(document_chunks)),
                    source_filter=source_name,
                    url_filter=[document.canonical_url],
                    similarity_threshold=-1.0,
                )
                oracle_chunks = rank_document_chunks(
                    case.question,
                    document.canonical_url,
                    vector_chunks,
                    document_chunks,
                    top_k=top_k,
                )
            oracle_latency_ms = (time.perf_counter() - started) * 1000
            oracle_rank = evidence_rank(gold, _ranked(oracle_chunks))

            rows.append(
                {
                    "case_id": case.id,
                    "variant_group": case.variant_group,
                    "question": case.question,
                    "gold_evidence_url": gold.url,
                    "pr11_root_cause": categories.get(case.id),
                    "global_evidence_rank": global_rank,
                    "oracle_evidence_rank": oracle_rank,
                    "global_latency_ms": global_latency_ms,
                    "oracle_latency_ms": oracle_latency_ms,
                    "production_metadata": metadata,
                    "global_top_5": [asdict(item) for item in global_items[:top_k]],
                    "oracle_top_5": [asdict(item) for item in _ranked(oracle_chunks)],
                }
            )

    global_summary = summarize_evidence_ranks(
        [row["global_evidence_rank"] for row in rows],
        [row["global_latency_ms"] for row in rows],
    )
    oracle_summary = summarize_evidence_ranks(
        [row["oracle_evidence_rank"] for row in rows],
        [row["oracle_latency_ms"] for row in rows],
    )
    report = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "branch": "eval/policy-hierarchical-retrieval",
        "manifest": str(manifest_path),
        "gold_evidence": str(evidence_path),
        "pr11_taxonomy": str(taxonomy_path),
        "cases": len(rows),
        "top_k": top_k,
        "embedding_batch_ms": embedding_batch_ms,
        "global": global_summary,
        "oracle_document": oracle_summary,
        "unresolved_pr11_categories": group_unresolved_categories(rows, categories),
        "decision_gate": {
            "metric": "oracle_document.evidence_hit_at_5",
            "target": 0.90,
            "result": architectural_decision(float(oracle_summary["evidence_hit_at_5"])),
        },
        "paid_semantic_answer_eval_performed": False,
    }
    return report, rows


def render_markdown(report: dict[str, Any]) -> str:
    global_summary = report["global"]
    oracle = report["oracle_document"]
    lines = [
        "# BuzzBot PR12 document-conditioned Policy retrieval",
        "",
        f"- Cases: {report['cases']}",
        f"- Final top-k: {report['top_k']}",
        "- Paid semantic answer evaluation: not run",
        "",
        "## Headline result",
        "",
        "| Mode | Evidence Hit@1 | Hit@3 | Hit@5 | MRR@5 | Mean ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| Global production | {global_summary['evidence_hit_at_1']:.1%} | "
        f"{global_summary['evidence_hit_at_3']:.1%} | {global_summary['evidence_hit_at_5']:.1%} | "
        f"{global_summary['evidence_mrr_at_5']:.3f} | "
        f"{global_summary['latency_ms']['mean']:.1f} | {global_summary['latency_ms']['p95']:.1f} |",
        f"| Oracle document | {oracle['evidence_hit_at_1']:.1%} | "
        f"{oracle['evidence_hit_at_3']:.1%} | {oracle['evidence_hit_at_5']:.1%} | "
        f"{oracle['evidence_mrr_at_5']:.3f} | {oracle['latency_ms']['mean']:.1f} | "
        f"{oracle['latency_ms']['p95']:.1f} |",
        "",
        "## Decision",
        "",
        f"- Gate: Oracle Evidence Hit@5 >= {report['decision_gate']['target']:.0%}",
        f"- Result: `{report['decision_gate']['result']}`",
        "",
        "## Previously unresolved PR11 categories",
        "",
        "| Root cause | Cases | Oracle Hit@1 | Hit@3 | Hit@5 | MRR@5 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for category, summary in report["unresolved_pr11_categories"].items():
        lines.append(
            f"| {category} | {summary['cases']} | {summary['evidence_hit_at_1']:.1%} | "
            f"{summary['evidence_hit_at_3']:.1%} | {summary['evidence_hit_at_5']:.1%} | "
            f"{summary['evidence_mrr_at_5']:.3f} |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare global and oracle-document Policy retrieval"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--evidence-file", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cases-output", type=Path, default=DEFAULT_CASES_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report, rows = asyncio.run(
        run(args.manifest, args.evidence_file, args.taxonomy, top_k=args.top_k)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.cases_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    args.cases_output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "global_evidence_hit_at_5": report["global"]["evidence_hit_at_5"],
                "oracle_evidence_hit_at_5": report["oracle_document"]["evidence_hit_at_5"],
                "decision": report["decision_gate"]["result"],
            }
        )
    )


if __name__ == "__main__":
    main()
