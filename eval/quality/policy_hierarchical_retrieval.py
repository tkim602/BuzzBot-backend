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

from app.rag.retrieval import (
    RetrievedChunk,
    get_text_embeddings,
    hybrid_retrieve,
    rerank_with_cross_encoder,
    vector_search,
)
from db.models import Chunk, Document, Source
from db.session import AsyncSessionLocal
from eval.quality.diagnose_policy_evidence import _source_filter
from eval.quality.evidence import evidence_rank, load_gold_evidence
from eval.quality.metrics import normalize_url
from eval.quality.policy_oracle_retrieval import (
    _chunk_from_model,
    _ranked,
    rank_document_chunks,
    summarize_evidence_ranks,
)
from eval.quality.schema import load_manifest_cases

DOCUMENT_COUNTS = (1, 2, 3, 5)
WITHIN_DOCUMENT_CANDIDATES = 15
FINAL_TOP_K = 5
MINIMUM_HIT_AT_5 = 0.85
STRETCH_HIT_AT_5 = 0.90

DEFAULT_MANIFEST = Path("eval/quality/manifests/dev_100.json")
DEFAULT_EVIDENCE = Path("eval/quality/gold_evidence/dev_100.json")
DEFAULT_PR12_REPORT = Path("eval/quality/policy_oracle_retrieval_pr12.json")
DEFAULT_PR12_CASES = Path("eval/quality/policy_oracle_retrieval_pr12_cases.jsonl")
DEFAULT_OUTPUT = Path("eval/quality/policy_hierarchical_retrieval_pr13.json")
DEFAULT_CASES_OUTPUT = Path("eval/quality/policy_hierarchical_retrieval_pr13_cases.jsonl")
DEFAULT_MARKDOWN = Path("docs/evals/policy_hierarchical_retrieval_pr13.md")


def select_document_urls(
    ranked_documents: list[RetrievedChunk], document_count: int
) -> tuple[str, ...]:
    if document_count not in DOCUMENT_COUNTS:
        raise ValueError(f"document_count must be one of {DOCUMENT_COUNTS}")
    selected: list[str] = []
    seen: set[str] = set()
    for chunk in ranked_documents:
        if not chunk.url:
            continue
        normalized = normalize_url(chunk.url)
        if normalized in seen:
            continue
        seen.add(normalized)
        selected.append(chunk.url)
        if len(selected) == document_count:
            break
    return tuple(selected)


def merge_document_candidates(
    query: str,
    selected_urls: tuple[str, ...],
    candidates: list[RetrievedChunk],
    *,
    top_k: int = FINAL_TOP_K,
) -> list[RetrievedChunk]:
    allowed = {normalize_url(url) for url in selected_urls}
    filtered = [
        replace(chunk) for chunk in candidates if chunk.url and normalize_url(chunk.url) in allowed
    ]
    if len(filtered) > 1:
        filtered = rerank_with_cross_encoder(query, filtered, top_k=top_k)
    return filtered[:top_k]


def select_candidate(summaries: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    passing = [
        (document_count, float(summary["evidence_hit_at_5"]))
        for document_count, summary in summaries.items()
        if float(summary["evidence_hit_at_5"]) >= MINIMUM_HIT_AT_5
    ]
    if not passing:
        return None
    best_hit = max(hit for _, hit in passing)
    document_count = min(count for count, hit in passing if hit == best_hit)
    return {
        "document_count": document_count,
        "evidence_hit_at_5": best_hit,
        "minimum_gate_passed": True,
        "stretch_gate_passed": best_hit >= STRETCH_HIT_AT_5,
    }


def _load_pr12_rows(path: Path, case_ids: set[str]) -> dict[str, dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    indexed = {str(row["case_id"]): row for row in rows}
    if len(rows) != 100 or set(indexed) != case_ids:
        raise ValueError(f"{path}: expected the same 100 manifest cases")
    return indexed


async def _load_documents(session, urls: set[str]):
    normalized_urls = {normalize_url(url) for url in urls}
    rows = (
        await session.execute(
            select(Document, Source.name).join(Source, Source.id == Document.source_id)
        )
    ).all()
    documents = {
        normalize_url(document.canonical_url): (document, source_name)
        for document, source_name in rows
        if normalize_url(document.canonical_url) in normalized_urls
    }
    chunks_by_url: dict[str, list[RetrievedChunk]] = defaultdict(list)
    doc_ids = [document.doc_id for document, _ in documents.values()]
    if doc_ids:
        chunks = (await session.scalars(select(Chunk).where(Chunk.doc_id.in_(doc_ids)))).all()
        source_by_doc = {
            document.doc_id: source_name for document, source_name in documents.values()
        }
        url_by_doc = {
            document.doc_id: normalize_url(document.canonical_url)
            for document, _ in documents.values()
        }
        for chunk in chunks:
            chunks_by_url[url_by_doc[chunk.doc_id]].append(
                _chunk_from_model(chunk, source_by_doc[chunk.doc_id])
            )
    return documents, chunks_by_url


def _document_rank(gold_url: str, ranked_documents: list[RetrievedChunk]) -> int | None:
    target = normalize_url(gold_url)
    for rank, url in enumerate(select_document_urls(ranked_documents, 5), start=1):
        if normalize_url(url) == target:
            return rank
    return None


def _summarize_mode(rows: list[dict[str, Any]], document_count: int) -> dict[str, Any]:
    values = [row["hierarchical"][str(document_count)] for row in rows]
    summary = summarize_evidence_ranks(
        [value["evidence_rank"] for value in values],
        [value["latency_ms"] for value in values],
    )
    summary["document_recall"] = mean(
        row["document_rank"] is not None and row["document_rank"] <= document_count for row in rows
    )
    global_hits = [
        row["global_evidence_rank"] is not None and row["global_evidence_rank"] <= 5 for row in rows
    ]
    candidate_hits = [
        value["evidence_rank"] is not None and value["evidence_rank"] <= 5 for value in values
    ]
    summary["wins_vs_global"] = sum(
        candidate and not baseline
        for candidate, baseline in zip(candidate_hits, global_hits, strict=True)
    )
    summary["regressions_vs_global"] = sum(
        baseline and not candidate
        for candidate, baseline in zip(candidate_hits, global_hits, strict=True)
    )
    return summary


async def run(
    manifest_path: Path = DEFAULT_MANIFEST,
    evidence_path: Path = DEFAULT_EVIDENCE,
    pr12_report_path: Path = DEFAULT_PR12_REPORT,
    pr12_cases_path: Path = DEFAULT_PR12_CASES,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases = load_manifest_cases(manifest_path)
    if len(cases) != 100:
        raise ValueError(f"{manifest_path}: expected 100 Policy dev cases")
    cases_by_id = {case.id: case for case in cases}
    gold_by_group = load_gold_evidence(evidence_path, cases)
    pr12_rows = _load_pr12_rows(pr12_cases_path, set(cases_by_id))
    pr12_report = json.loads(pr12_report_path.read_text(encoding="utf-8"))

    embedding_started = time.perf_counter()
    embeddings = await get_text_embeddings([case.question for case in cases])
    embedding_batch_ms = (time.perf_counter() - embedding_started) * 1000
    if len(embeddings) != len(cases):
        raise RuntimeError("embedding result count does not match manifest")

    stage1: dict[str, tuple[list[RetrievedChunk], float]] = {}
    async with AsyncSessionLocal() as session:
        for case, embedding in zip(cases, embeddings, strict=True):
            started = time.perf_counter()
            ranked_documents = await hybrid_retrieve(
                session,
                case.question,
                embedding,
                top_k=5,
                source_filter=_source_filter(case.question),
                force_fts=True,
                max_chunks_per_url=1,
            )
            stage1[case.id] = (
                ranked_documents,
                (time.perf_counter() - started) * 1000,
            )

        selected_urls = {
            url
            for ranked_documents, _ in stage1.values()
            for url in select_document_urls(ranked_documents, 5)
        }
        documents, chunks_by_url = await _load_documents(session, selected_urls)

        rows: list[dict[str, Any]] = []
        for case, embedding in zip(cases, embeddings, strict=True):
            gold = gold_by_group[case.variant_group]
            ranked_documents, stage1_latency_ms = stage1[case.id]
            row: dict[str, Any] = {
                "case_id": case.id,
                "variant_group": case.variant_group,
                "question": case.question,
                "gold_evidence_url": gold.url,
                "global_evidence_rank": pr12_rows[case.id]["global_evidence_rank"],
                "oracle_evidence_rank": pr12_rows[case.id]["oracle_evidence_rank"],
                "document_rank": _document_rank(gold.url, ranked_documents),
                "stage1_top_5_urls": list(select_document_urls(ranked_documents, 5)),
                "hierarchical": {},
            }
            for document_count in DOCUMENT_COUNTS:
                started = time.perf_counter()
                urls = select_document_urls(ranked_documents, document_count)
                resolved = [
                    documents[normalize_url(url)] for url in urls if normalize_url(url) in documents
                ]
                canonical_urls = [document.canonical_url for document, _ in resolved]
                source_names = list(dict.fromkeys(source_name for _, source_name in resolved))
                total_chunks = sum(
                    len(chunks_by_url[normalize_url(document.canonical_url)])
                    for document, _ in resolved
                )
                vector_chunks = (
                    await vector_search(
                        session,
                        embedding,
                        top_k=max(1, total_chunks),
                        source_filter=source_names,
                        url_filter=canonical_urls,
                        similarity_threshold=-1.0,
                    )
                    if resolved
                    else []
                )
                candidates: list[RetrievedChunk] = []
                for document, _ in resolved:
                    normalized = normalize_url(document.canonical_url)
                    candidates.extend(
                        rank_document_chunks(
                            case.question,
                            document.canonical_url,
                            vector_chunks,
                            chunks_by_url[normalized],
                            top_k=WITHIN_DOCUMENT_CANDIDATES,
                            rerank=False,
                        )
                    )
                final_chunks = merge_document_candidates(case.question, urls, candidates)
                latency_ms = stage1_latency_ms + (time.perf_counter() - started) * 1000
                row["hierarchical"][str(document_count)] = {
                    "selected_urls": list(urls),
                    "evidence_rank": evidence_rank(gold, _ranked(final_chunks)),
                    "latency_ms": latency_ms,
                    "top_5": [asdict(item) for item in _ranked(final_chunks)],
                }
            rows.append(row)

    summaries = {
        document_count: _summarize_mode(rows, document_count) for document_count in DOCUMENT_COUNTS
    }
    candidate = select_candidate(summaries)
    report = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "branch": "eval/policy-hierarchical-prototype",
        "manifest": str(manifest_path),
        "gold_evidence": str(evidence_path),
        "cases": len(rows),
        "document_counts": list(DOCUMENT_COUNTS),
        "within_document_candidates": WITHIN_DOCUMENT_CANDIDATES,
        "final_top_k": FINAL_TOP_K,
        "embedding_batch_ms": embedding_batch_ms,
        "production_baseline": pr12_report["global"],
        "oracle_ceiling": pr12_report["oracle_document"],
        "hierarchical": {str(key): value for key, value in summaries.items()},
        "candidate": candidate,
        "gates": {
            "minimum_evidence_hit_at_5": MINIMUM_HIT_AT_5,
            "stretch_evidence_hit_at_5": STRETCH_HIT_AT_5,
        },
        "production_switch_performed": False,
        "paid_semantic_answer_eval_performed": False,
    }
    return report, rows


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BuzzBot PR13 evaluation-only hierarchical Policy retrieval",
        "",
        f"- Cases: {report['cases']}",
        f"- Production Evidence Hit@5: {report['production_baseline']['evidence_hit_at_5']:.1%}",
        f"- Oracle-document Evidence Hit@5: {report['oracle_ceiling']['evidence_hit_at_5']:.1%}",
        "- Production switch: not performed",
        "- Paid semantic answer evaluation: not performed",
        "",
        "## Bounded document-count comparison",
        "",
        "| Documents | Doc recall | Evidence Hit@1 | Hit@3 | Hit@5 | MRR@5 | Wins | Regressions | Mean ms | p95 ms |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for document_count in report["document_counts"]:
        summary = report["hierarchical"][str(document_count)]
        lines.append(
            f"| {document_count} | {summary['document_recall']:.1%} | "
            f"{summary['evidence_hit_at_1']:.1%} | {summary['evidence_hit_at_3']:.1%} | "
            f"{summary['evidence_hit_at_5']:.1%} | {summary['evidence_mrr_at_5']:.3f} | "
            f"{summary['wins_vs_global']} | {summary['regressions_vs_global']} | "
            f"{summary['latency_ms']['mean']:.1f} | {summary['latency_ms']['p95']:.1f} |"
        )
    lines.extend(["", "## Decision", ""])
    if report["candidate"]:
        candidate = report["candidate"]
        lines.extend(
            [
                f"- Candidate document count: **{candidate['document_count']}**",
                f"- Evidence Hit@5: **{candidate['evidence_hit_at_5']:.1%}**",
                "- Minimum 85% gate: **PASS**",
                f"- Stretch 90% gate: **{'PASS' if candidate['stretch_gate_passed'] else 'FAIL'}**",
            ]
        )
    else:
        lines.append("- No hierarchical candidate met the 85% Evidence Hit@5 gate.")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare bounded evaluation-only hierarchical Policy retrieval"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--evidence-file", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--pr12-report", type=Path, default=DEFAULT_PR12_REPORT)
    parser.add_argument("--pr12-cases", type=Path, default=DEFAULT_PR12_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cases-output", type=Path, default=DEFAULT_CASES_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report, rows = asyncio.run(
        run(args.manifest, args.evidence_file, args.pr12_report, args.pr12_cases)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.cases_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    args.cases_output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"candidate": report["candidate"], "hierarchical": report["hierarchical"]}))


if __name__ == "__main__":
    main()
