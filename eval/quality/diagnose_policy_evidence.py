from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.rag.retrieval import (
    RetrievedChunk,
    _cap_chunks_per_url,
    _extract_query_hints,
    _lexical_match_score,
    _rrf_fuse_results,
    _signal_match_count,
    fts_search,
    get_text_embeddings,
    hybrid_retrieve,
    vector_search,
)
from app.retrieval.documents import (
    OFFICIAL_SOURCE_NAMES,
    SOURCE_NAMES_BY_TYPE,
    policy_source_types,
)
from db.models import Chunk, Document, Source
from db.session import AsyncSessionLocal
from eval.quality.evidence import (
    GoldEvidence,
    evidence_rank,
    load_gold_evidence,
    normalize_evidence,
)
from eval.quality.metrics import RankedItem, first_gold_rank, normalize_url
from eval.quality.schema import GoldCase, load_manifest_cases

DEFAULT_SNAPSHOT = Path("eval/frozen/policy_answer_dev_100_v1/snapshot.json")
DEFAULT_MANIFEST = Path("eval/quality/manifests/dev_100.json")
DEFAULT_EVIDENCE = Path("eval/quality/gold_evidence/dev_100.json")
DEFAULT_RETRIEVAL = Path("eval/quality/reports_policy_pr11_baseline/latest_cases.jsonl")
DEFAULT_JSON = Path("eval/quality/policy_evidence_miss_diagnosis_pr11.json")
DEFAULT_MARKDOWN = Path("docs/evals/policy_evidence_miss_diagnosis_pr11.md")


def classify_root_cause(
    *,
    gold_span: bool,
    document_exists: bool,
    span_in_document: bool,
    span_in_chunk: bool,
    source_in_route: bool,
    production_evidence_rank: int | None,
    production_document_rank: int | None,
    parent_evidence_rank: int | None,
    parent_document_rank: int | None,
    candidate_generated: bool,
    pre_rerank_evidence_rank: int | None,
    deep_vector_evidence_rank: int | None,
    deep_fts_and_evidence_rank: int | None,
    deep_fts_or_evidence_rank: int | None,
) -> tuple[str, str]:
    if not gold_span:
        return "GOLD_OR_EVAL_DEFINITION_ISSUE", "gold_definition"
    if not document_exists:
        return "DOCUMENT_NOT_INDEXED", "corpus_source_availability"
    if not span_in_document:
        return "STALE_OR_INCOMPLETE_INGESTION", "corpus_source_availability"
    if not span_in_chunk:
        return "CHUNKING_BOUNDARY_LOSS", "chunk_availability"
    if not source_in_route:
        return "SOURCE_ROUTING_LOSS", "candidate_generation"
    if production_evidence_rank is not None and production_evidence_rank <= 5:
        return "RESOLVED_SINCE_FREEZE", "resolved"
    if parent_evidence_rank is not None and parent_evidence_rank <= 5:
        return "CHILD_RESELECTION_LOSS", "child_reselection"
    if any(
        rank is not None and rank <= 5 for rank in (production_document_rank, parent_document_rank)
    ):
        return "DOCUMENT_RETRIEVED_WRONG_CHUNK", "chunk_selection"
    if candidate_generated or pre_rerank_evidence_rank is not None:
        return "FUSION_OR_RERANK_LOSS", "fusion_rerank_top_k"
    if any(
        rank is not None
        for rank in (
            deep_vector_evidence_rank,
            deep_fts_and_evidence_rank,
            deep_fts_or_evidence_rank,
        )
    ):
        return "CANDIDATE_GENERATION_TRUNCATION", "candidate_generation"
    return "QUERY_TERM_MISMATCH", "candidate_generation"


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


def _source_filter(question: str) -> list[str]:
    requested = policy_source_types(question)
    if not requested:
        return OFFICIAL_SOURCE_NAMES
    return list(
        dict.fromkeys(
            name
            for source_type in requested
            for name in (
                (SOURCE_NAMES_BY_TYPE[source_type],)
                if isinstance(SOURCE_NAMES_BY_TYPE[source_type], str)
                else SOURCE_NAMES_BY_TYPE[source_type]
            )
        )
    )


def _load_production_rows(path: Path) -> dict[str, dict[str, Any]]:
    rows = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return {row["case_id"]: row for row in rows if row["mode"] == "production"}


def _rationale(root_cause: str) -> str:
    return {
        "GOLD_OR_EVAL_DEFINITION_ISSUE": "The committed gold marks this fact as missing decisive corpus evidence.",
        "DOCUMENT_NOT_INDEXED": "The authoritative gold document is absent from the indexed corpus.",
        "STALE_OR_INCOMPLETE_INGESTION": "The gold document exists, but its indexed document text lacks the decisive span.",
        "CHUNKING_BOUNDARY_LOSS": "The decisive span exists in document text but not in any single indexed chunk.",
        "SOURCE_ROUTING_LOSS": "The query route excludes the source that owns the decisive evidence.",
        "RESOLVED_SINCE_FREEZE": "The current source-run top five contains the decisive span that was absent in the frozen PR10 result.",
        "CHILD_RESELECTION_LOSS": "The parent hybrid result contains the decisive chunk, but child reselection discards it.",
        "DOCUMENT_RETRIEVED_WRONG_CHUNK": "The current top five contains the gold document but selects a different chunk from it.",
        "FUSION_OR_RERANK_LOSS": "The bounded production candidate pool contains the decisive chunk, but fusion or reranking drops it.",
        "CANDIDATE_GENERATION_TRUNCATION": "The decisive chunk is only present below the bounded production vector or diversified lexical candidate pool.",
        "QUERY_TERM_MISMATCH": "The decisive chunk is indexed and in scope but absent from routed vector and lexical top 200.",
    }[root_cause]


def _proposed_fix(root_cause: str) -> str:
    return {
        "GOLD_OR_EVAL_DEFINITION_ISSUE": "no production fix required; resolve the source/eval gap separately",
        "DOCUMENT_NOT_INDEXED": "add the bounded official source through the existing registry",
        "STALE_OR_INCOMPLETE_INGESTION": "repair and safely reindex the affected official source",
        "CHUNKING_BOUNDARY_LOSS": "preserve the relationship in a general-purpose chunk without increasing chunk size",
        "SOURCE_ROUTING_LOSS": "correct the generic source-routing cue",
        "RESOLVED_SINCE_FREEZE": "no production fix required",
        "CHILD_RESELECTION_LOSS": "rerank parent and bounded child candidates together before final truncation",
        "DOCUMENT_RETRIEVED_WRONG_CHUNK": "rank child chunks by combined semantic and lexical relevance within the selected document",
        "FUSION_OR_RERANK_LOSS": "retain semantically strongest generated evidence through existing fusion/reranking",
        "CANDIDATE_GENERATION_TRUNCATION": "improve the bounded candidate pool without increasing final answer top-k",
        "QUERY_TERM_MISMATCH": "improve generic candidate generation for the observed wording gap",
    }[root_cause]


async def diagnose(
    snapshot_path: Path,
    manifest_path: Path,
    evidence_path: Path,
    retrieval_path: Path,
    *,
    deep_limit: int = 200,
) -> dict[str, Any]:
    cases = load_manifest_cases(manifest_path)
    cases_by_id = {case.id: case for case in cases}
    gold_by_group = load_gold_evidence(evidence_path, cases)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    frozen_misses = [row for row in snapshot["cases"] if not row["metadata"]["evidence_hit_at_5"]]
    if len(frozen_misses) != 30:
        raise ValueError(f"expected 30 frozen evidence misses, found {len(frozen_misses)}")

    production_rows = _load_production_rows(retrieval_path)
    embeddings = await get_text_embeddings(
        [cases_by_id[row["case_id"]].question for row in frozen_misses]
    )
    if len(embeddings) != len(frozen_misses):
        raise RuntimeError("embedding result count does not match frozen misses")

    async with AsyncSessionLocal() as session:
        documents = list((await session.scalars(select(Document))).all())
        documents_by_url = {
            normalize_url(document.canonical_url): document for document in documents
        }
        sources = {
            source.id: source.name for source in (await session.scalars(select(Source))).all()
        }
        needed_doc_ids = {
            document.doc_id
            for row in frozen_misses
            if (
                document := documents_by_url.get(
                    normalize_url(gold_by_group[cases_by_id[row["case_id"]].variant_group].url)
                )
            )
        }
        chunks_by_doc: dict[Any, list[Chunk]] = {doc_id: [] for doc_id in needed_doc_ids}
        if needed_doc_ids:
            for chunk in (
                await session.scalars(select(Chunk).where(Chunk.doc_id.in_(needed_doc_ids)))
            ).all():
                chunks_by_doc[chunk.doc_id].append(chunk)

        rows: list[dict[str, Any]] = []
        for frozen, embedding in zip(frozen_misses, embeddings, strict=True):
            case: GoldCase = cases_by_id[frozen["case_id"]]
            gold: GoldEvidence = gold_by_group[case.variant_group]
            document = documents_by_url.get(normalize_url(gold.url))
            source_name = sources.get(document.source_id) if document else None
            needle = normalize_evidence(gold.span)
            document_exists = document is not None
            span_in_document = bool(
                document and needle and needle in normalize_evidence(document.content_text)
            )
            document_chunks = chunks_by_doc.get(document.doc_id, []) if document else []
            span_in_chunk = bool(
                needle
                and any(needle in normalize_evidence(chunk.chunk_text) for chunk in document_chunks)
            )
            source_filter = _source_filter(case.question)
            source_in_route = bool(source_name and source_name in source_filter)

            vector = await vector_search(
                session,
                embedding,
                top_k=deep_limit,
                source_filter=source_filter,
                similarity_threshold=-1.0,
            )
            fts_and = await fts_search(
                session, case.question, top_k=deep_limit, source_filter=source_filter
            )
            fts_or = await fts_search(
                session,
                case.question,
                top_k=deep_limit,
                source_filter=source_filter,
                match_any=True,
            )
            parent = await hybrid_retrieve(
                session,
                case.question,
                embedding,
                top_k=5,
                source_filter=source_filter,
                force_fts=True,
                max_chunks_per_url=1,
            )
            production_vector = [chunk for chunk in vector if chunk.score >= 0.3][:5]
            production_fts = list(fts_or)
            production_fts.sort(
                key=lambda chunk: (_lexical_match_score(case.question, chunk), chunk.score),
                reverse=True,
            )
            production_fts = _cap_chunks_per_url(production_fts, max_chunks_per_url=1, top_k=15)
            pre_rerank = _rrf_fuse_results(production_vector, production_fts, top_k=15)
            hints = _extract_query_hints(case.question)
            pre_rerank.sort(
                key=lambda chunk: (
                    _signal_match_count(chunk, hints),
                    _lexical_match_score(case.question, chunk),
                    chunk.score,
                ),
                reverse=True,
            )
            current = production_rows[case.id]
            ranks = {
                "production": current.get("evidence_rank"),
                "production_document": current.get("rank"),
                "parent_document": first_gold_rank(case, _ranked(parent)),
                "parent_evidence": evidence_rank(gold, _ranked(parent)),
                "pre_rerank_evidence": evidence_rank(gold, _ranked(pre_rerank)),
                "vector_pool_evidence": evidence_rank(gold, _ranked(production_vector)),
                "fts_pool_evidence": evidence_rank(gold, _ranked(production_fts)),
                "deep_vector_evidence": evidence_rank(gold, _ranked(vector)),
                "deep_fts_and_evidence": evidence_rank(gold, _ranked(fts_and)),
                "deep_fts_or_evidence": evidence_rank(gold, _ranked(fts_or)),
            }
            candidate_generated = any(
                ranks[name] is not None for name in ("vector_pool_evidence", "fts_pool_evidence")
            )
            root_cause, failure_stage = classify_root_cause(
                gold_span=gold.span is not None,
                document_exists=document_exists,
                span_in_document=span_in_document,
                span_in_chunk=span_in_chunk,
                source_in_route=source_in_route,
                production_evidence_rank=ranks["production"],
                production_document_rank=ranks["production_document"],
                parent_evidence_rank=ranks["parent_evidence"],
                parent_document_rank=ranks["parent_document"],
                candidate_generated=candidate_generated,
                pre_rerank_evidence_rank=ranks["pre_rerank_evidence"],
                deep_vector_evidence_rank=ranks["deep_vector_evidence"],
                deep_fts_and_evidence_rank=ranks["deep_fts_and_evidence"],
                deep_fts_or_evidence_rank=ranks["deep_fts_or_evidence"],
            )
            rows.append(
                {
                    "case_id": case.id,
                    "question": case.question,
                    "gold_urls": list(case.gold_urls),
                    "gold_evidence_url": gold.url,
                    "frozen_top_5": frozen["evidence"][:5],
                    "current_top_5": current["retrieved"][:5],
                    "document_hit_at_5": bool(frozen["metadata"]["document_hit_at_5"]),
                    "evidence_hit_at_5": False,
                    "root_cause": root_cause,
                    "failure_stage": failure_stage,
                    "rationale": _rationale(root_cause),
                    "decisive_source_exists_in_corpus": document_exists,
                    "decisive_text_exists_in_document": span_in_document,
                    "decisive_text_exists_in_indexed_chunk": span_in_chunk,
                    "decisive_candidate_generated": candidate_generated,
                    "candidate_ranks": ranks,
                    "source_name": source_name,
                    "routed_source_names": source_filter,
                    "proposed_fix": _proposed_fix(root_cause),
                }
            )

    return {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "exactly the 30 frozen PR10 Policy evidence misses",
        "snapshot": str(snapshot_path),
        "manifest": str(manifest_path),
        "gold_evidence": str(evidence_path),
        "current_retrieval_report": str(retrieval_path),
        "deep_candidate_limit": deep_limit,
        "cases": rows,
        "root_cause_counts": dict(sorted(Counter(row["root_cause"] for row in rows).items())),
        "failure_stage_counts": dict(sorted(Counter(row["failure_stage"] for row in rows).items())),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BuzzBot PR11 Policy evidence-miss diagnosis",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Scope: {report['scope']}",
        f"- Cases: {len(report['cases'])}",
        "",
        "## Root-cause distribution",
        "",
        "| Root cause | Cases |",
        "|---|---:|",
    ]
    lines.extend(f"| {name} | {count} |" for name, count in report["root_cause_counts"].items())
    lines.extend(
        [
            "",
            "## Case diagnosis",
            "",
            "| Case | Root cause | Earliest failed stage | Candidate ranks (production pool / pre-rerank / deep vector / deep FTS OR) |",
            "|---|---|---|---|",
        ]
    )
    for row in report["cases"]:
        ranks = row["candidate_ranks"]
        lines.append(
            f"| {row['case_id']} | {row['root_cause']} | {row['failure_stage']} | "
            f"{ranks['vector_pool_evidence'] or ranks['fts_pool_evidence']} / "
            f"{ranks['pre_rerank_evidence']} / {ranks['deep_vector_evidence']} / "
            f"{ranks['deep_fts_or_evidence']} |"
        )
    lines.extend(
        [
            "",
            "`document_hit_at_5` means a gold URL is present in the first five results. "
            "`evidence_hit_at_5` additionally requires the committed decisive span to occur "
            "in a returned chunk from that URL.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose frozen PR10 Policy evidence misses")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--evidence-file", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--retrieval-report", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--deep-limit", type=int, default=200)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(
        diagnose(
            args.snapshot,
            args.manifest,
            args.evidence_file,
            args.retrieval_report,
            deep_limit=args.deep_limit,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {"cases": len(report["cases"]), "root_cause_counts": report["root_cause_counts"]}
        )
    )


if __name__ == "__main__":
    main()
