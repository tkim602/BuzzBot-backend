from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import Chunk, Document, Source
from eval.quality.metrics import normalize_url
from eval.quality.runner import MODES
from eval.quality.schema import GoldCase, load_manifest_cases


@dataclass(frozen=True)
class CorpusPresence:
    case_id: str
    gold_urls: tuple[str, ...]
    matched_gold_urls: tuple[str, ...]
    indexed: bool
    matching_document_ids: tuple[str, ...]
    matching_chunk_count: int


async def indexed_gold_presence(session, cases: list[GoldCase]) -> dict[str, CorpusPresence]:
    rows = (
        await session.execute(
            select(
                Document.doc_id,
                Document.canonical_url,
                func.count(Chunk.chunk_id).label("chunk_count"),
            )
            .join(Source, Source.id == Document.source_id)
            .outerjoin(Chunk, Chunk.doc_id == Document.doc_id)
            .where(Source.allowed.is_(True))
            .group_by(Document.doc_id)
        )
    ).all()
    by_url = {normalize_url(row.canonical_url): row for row in rows}
    result: dict[str, CorpusPresence] = {}
    for case in cases:
        matches = [
            (url, by_url[normalize_url(url)])
            for url in case.gold_urls
            if normalize_url(url) in by_url and int(by_url[normalize_url(url)].chunk_count or 0) > 0
        ]
        result[case.id] = CorpusPresence(
            case_id=case.id,
            gold_urls=case.gold_urls,
            matched_gold_urls=tuple(url for url, _ in matches),
            indexed=bool(matches),
            matching_document_ids=tuple(str(row.doc_id) for _, row in matches),
            matching_chunk_count=sum(int(row.chunk_count or 0) for _, row in matches),
        )
    return result


def validate_report_alignment(
    manifest_cases: list[GoldCase],
    retrieval_rows: list[dict[str, object]],
    chat_rows: list[dict[str, object]],
) -> tuple[dict[str, dict[str, dict[str, object]]], dict[str, dict[str, object]]]:
    manifest_ids = [case.id for case in manifest_cases]
    if len(manifest_ids) != 100 or len(set(manifest_ids)) != 100:
        raise ValueError("dev manifest must contain exactly 100 unique case IDs")

    retrieval: dict[str, dict[str, dict[str, object]]] = {}
    for row in retrieval_rows:
        case_id, mode = str(row.get("case_id", "")), str(row.get("mode", ""))
        modes = retrieval.setdefault(case_id, {})
        if mode in modes:
            raise ValueError(f"duplicate retrieval row: {case_id}/{mode}")
        modes[mode] = row

    chat: dict[str, dict[str, object]] = {}
    for row in chat_rows:
        case_id = str(row.get("case_id", ""))
        if case_id in chat:
            raise ValueError(f"duplicate chat row: {case_id}")
        chat[case_id] = row

    expected = set(manifest_ids)
    if set(retrieval) != expected or set(chat) != expected:
        raise ValueError("manifest/retrieval/chat case-ID set mismatch")
    for case_id, modes in retrieval.items():
        if set(modes) != set(MODES):
            raise ValueError(f"retrieval modes incomplete for {case_id}")
    return retrieval, chat


def _rank(row: dict[str, object] | None) -> int | None:
    value = row.get("rank") if row else None
    return int(value) if isinstance(value, int | float) else None


def diagnose_case(
    case: GoldCase,
    corpus: CorpusPresence,
    retrieval: dict[str, dict[str, object]],
    chat: dict[str, object],
) -> dict[str, object]:
    required_chat = {"correct", "supported", "abstained", "citation_gold_hit"}
    data_error = set(retrieval) != set(MODES) or not required_chat <= set(chat)
    ranks = {mode: _rank(retrieval.get(mode)) for mode in MODES}
    hit_at_5 = ranks["production"] is not None and ranks["production"] <= 5
    correct, supported = bool(chat.get("correct")), bool(chat.get("supported"))

    secondary: list[str] = []
    if data_error:
        primary = "DATA_ERROR"
    elif not corpus.indexed:
        primary = "A"
    elif not hit_at_5:
        primary = "B"
    elif correct and supported:
        primary = "PASS"
    else:
        primary = "C"
        if chat.get("abstained"):
            secondary.append("C_ABSTAIN_WITH_GOLD_RETRIEVED")
        if not chat.get("citation_gold_hit"):
            secondary.append("C_GOLD_RETRIEVED_NOT_CITED")
        if not correct:
            secondary.append("C_ANSWER_INCORRECT")
        if not supported:
            secondary.append("C_ANSWER_UNSUPPORTED")

    result = {
        "case_id": case.id,
        "variant_group": case.variant_group,
        "question": case.question,
        "gold_urls": list(case.gold_urls),
        "gold_sources": list(case.gold_sources),
        "vertical": case.gold_vertical,
        "question_type": case.question_type,
        "difficulty": case.difficulty,
        "style": case.style,
        "time_sensitive": case.time_sensitive,
        "primary_class": primary,
        "secondary_reasons": secondary,
        "corpus_present": corpus.indexed,
        "matched_gold_urls": list(corpus.matched_gold_urls),
        "matching_document_ids": list(corpus.matching_document_ids),
        "matching_chunk_count": corpus.matching_chunk_count,
        "production_rank": ranks["production"],
        "raw_rank": ranks["raw"],
        "vector_rank": ranks["vector"],
        "fts_rank": ranks["fts"],
        "hit_at_5": hit_at_5,
        "correct": correct,
        "supported": supported,
        "abstained": bool(chat.get("abstained")),
        "confidence": float(chat.get("confidence") or 0.0),
        "citation_gold_hit": bool(chat.get("citation_gold_hit")),
    }
    result["retrieval_miss_subtype"] = classify_retrieval_miss(result) if primary == "B" else None
    return result


def classify_retrieval_miss(row: dict[str, object]) -> str:
    def top5(rank: object) -> bool:
        return isinstance(rank, int | float) and rank <= 5

    component = {
        "raw": row.get("raw_rank"),
        "vector": row.get("vector_rank"),
        "fts": row.get("fts_rank"),
    }
    surfaced = {name for name, rank in component.items() if top5(rank)}
    if surfaced == {"vector"}:
        return "B_VECTOR_ONLY"
    if surfaced == {"fts"}:
        return "B_FTS_ONLY"
    if surfaced:
        return "B_FUSION_OR_RERANK_LOSS"
    if any(isinstance(rank, int | float) and rank <= 10 for rank in component.values()):
        return "B_RAW_BELOW_5"
    if all(rank is None for rank in component.values()):
        return "B_NONE"
    return "B_OTHER"


def _rate(rows: list[dict[str, object]], key: str) -> float | None:
    return sum(bool(row.get(key)) for row in rows) / len(rows) if rows else None


def _bucket_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    counts = Counter(str(row["primary_class"]) for row in rows)
    return {
        "cases": len(rows),
        **{stage: counts.get(stage, 0) for stage in ("A", "B", "C", "PASS", "DATA_ERROR")},
        "correctness": _rate(rows, "correct"),
        "support": _rate(rows, "supported"),
        "abstention": _rate(rows, "abstained"),
        "citation_gold_hit": _rate(rows, "citation_gold_hit"),
        "corpus_coverage": _rate(rows, "corpus_present"),
    }


def _rank_bucket(rank: object) -> str:
    if rank == 1:
        return "rank_1"
    if isinstance(rank, int | float) and rank <= 3:
        return "rank_2_3"
    if isinstance(rank, int | float) and rank <= 5:
        return "rank_4_5"
    if isinstance(rank, int | float) and rank <= 10:
        return "rank_6_10"
    return "rank_gt_10_or_missing"


def summarize_diagnoses(rows: list[dict[str, object]]) -> dict[str, object]:
    counts = Counter(str(row["primary_class"]) for row in rows)
    stages = ("A", "B", "C", "PASS", "DATA_ERROR")
    hit = [row for row in rows if row.get("hit_at_5")]
    miss = [row for row in rows if not row.get("hit_at_5")]
    conditionals = {
        name: {
            "cases": len(bucket),
            "abstained": _rate(bucket, "abstained"),
            "correct": _rate(bucket, "correct"),
            "supported": _rate(bucket, "supported"),
            "citation_gold_hit": _rate(bucket, "citation_gold_hit"),
        }
        for name, bucket in (("hit_at_5", hit), ("miss_at_5", miss))
    }
    rank_buckets = {
        name: _bucket_summary([row for row in rows if _rank_bucket(row["production_rank"]) == name])
        for name in (
            "rank_1",
            "rank_2_3",
            "rank_4_5",
            "rank_6_10",
            "rank_gt_10_or_missing",
        )
    }
    breakdowns: dict[str, dict[str, object]] = {}
    for field in ("vertical", "question_type", "difficulty", "style", "time_sensitive"):
        values = sorted({str(row[field]) for row in rows})
        breakdowns[field] = {
            value: _bucket_summary([row for row in rows if str(row[field]) == value])
            for value in values
        }
    total = len(rows)
    failures = [row for row in rows if row["primary_class"] in {"A", "B", "C"}]

    def top_counts(field: str, *, many: bool = False) -> list[dict[str, object]]:
        values = Counter(
            value
            for row in failures
            for value in (
                [str(item) for item in row.get(field, [])]
                if many
                else [str(row.get(field, "unknown"))]
            )
        )
        return [
            {"name": name, "cases": count}
            for name, count in sorted(values.items(), key=lambda item: (-item[1], item[0]))
        ]

    failed_counts = {stage: counts.get(stage, 0) for stage in ("A", "B", "C")}
    retrieval_misses = [row for row in rows if row["primary_class"] == "B"]

    def miss_counts(field: str, *, many: bool = False) -> dict[str, int]:
        return dict(
            sorted(
                Counter(
                    value
                    for row in retrieval_misses
                    for value in (
                        [str(item) for item in row.get(field, [])]
                        if many
                        else [str(row.get(field, "unknown"))]
                    )
                ).items()
            )
        )

    dominant = max(failed_counts, key=failed_counts.get) if failures else None
    recommendations = {
        "A": "corpus/source coverage is the first bottleneck",
        "B": "retrieval/ranking is the first bottleneck",
        "C": "answer synthesis/citation/validation is the first bottleneck",
    }
    return {
        "cases": total,
        "actual_indexed_gold_coverage": _rate(rows, "corpus_present"),
        "primary_counts": {stage: counts.get(stage, 0) for stage in stages},
        "primary_rates": {
            stage: counts.get(stage, 0) / total if total else 0.0 for stage in stages
        },
        "conditional": conditionals,
        "rank_buckets": rank_buckets,
        "breakdowns": breakdowns,
        "retrieval_miss_subtypes": dict(
            sorted(
                Counter(
                    str(row["retrieval_miss_subtype"])
                    for row in rows
                    if row["primary_class"] == "B"
                ).items()
            )
        ),
        "retrieval_miss_breakdowns": {
            "vertical": miss_counts("vertical"),
            "question_type": miss_counts("question_type"),
            "gold_source": miss_counts("gold_sources", many=True),
        },
        "top_failed_verticals": top_counts("vertical"),
        "top_failed_question_types": top_counts("question_type"),
        "top_failed_gold_sources": top_counts("gold_sources", many=True),
        "dominant_bottleneck": dominant,
        "next_action": recommendations.get(dominant, "no failed cases"),
    }


def _percent(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.2%}"


def write_reports(
    report_dir: Path,
    rows: list[dict[str, object]],
    summary: dict[str, object],
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    with (report_dir / "latest_cases.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (report_dir / "latest_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    counts, rates = summary["primary_counts"], summary["primary_rates"]
    conditional = summary["conditional"]
    lines = [
        "# BuzzBot dev-100 failure diagnosis",
        "",
        "- No paid API or network calls were made by this diagnosis.",
        (
            "- `document_coverage` means at least one matching Document row; "
            "evidence coverage is reported separately by the retrieval evaluator."
        ),
        (
            "- Actual indexed gold corpus coverage: "
            f"{_percent(summary['actual_indexed_gold_coverage'])}"
        ),
        "",
        "## Primary failure stage",
        "",
        "| Stage | Cases | Rate |",
        "|---|---:|---:|",
    ]
    for stage in ("A", "B", "C", "PASS", "DATA_ERROR"):
        lines.append(f"| {stage} | {counts[stage]} | {_percent(rates[stage])} |")
    lines.extend(
        [
            "",
            "## Retrieval association",
            "",
            "| Metric | Hit@5 | Miss@5 |",
            "|---|---:|---:|",
        ]
    )
    for label, key in (
        ("Abstained", "abstained"),
        ("Correct", "correct"),
        ("Supported", "supported"),
        ("Gold citation", "citation_gold_hit"),
    ):
        lines.append(
            f"| {label} | {_percent(conditional['hit_at_5'][key])} | "
            f"{_percent(conditional['miss_at_5'][key])} |"
        )
    lines.extend(["", "## Retrieval-miss subtypes", ""])
    for name, count in summary["retrieval_miss_subtypes"].items():
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Vertical A/B/C distribution", ""])
    lines.extend(
        [
            "| Vertical | Cases | A | B | C | PASS |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name, bucket in summary["breakdowns"]["vertical"].items():
        lines.append(
            f"| {name} | {bucket['cases']} | {bucket['A']} | {bucket['B']} | "
            f"{bucket['C']} | {bucket['PASS']} |"
        )
    lines.extend(["", "## Top affected official sources", ""])
    for item in summary["top_failed_gold_sources"]:
        lines.append(f"- {item['name']}: {item['cases']}")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Dominant bottleneck: {summary['dominant_bottleneck'] or 'none'}",
            f"- Next action: {summary['next_action']}",
            "",
        ]
    )
    (report_dir / "latest_summary.md").write_text("\n".join(lines), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


async def run_diagnosis(
    manifest: Path,
    retrieval_report: Path,
    chat_report: Path,
    report_dir: Path,
) -> dict[str, object]:
    from app.db.session import AsyncSessionLocal

    cases = load_manifest_cases(manifest)
    retrieval, chat = validate_report_alignment(
        cases,
        _read_jsonl(retrieval_report),
        _read_jsonl(chat_report),
    )
    async with AsyncSessionLocal() as session:
        corpus = await indexed_gold_presence(session, cases)
    rows = [
        diagnose_case(case, corpus[case.id], retrieval[case.id], chat[case.id]) for case in cases
    ]
    summary = summarize_diagnoses(rows)
    summary.update(
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "manifest": str(manifest),
            "retrieval_report": str(retrieval_report),
            "chat_report": str(chat_report),
        }
    )
    write_reports(report_dir, rows, summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose fixed dev-100 failures offline")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--retrieval-report", type=Path, required=True)
    parser.add_argument("--chat-report", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = asyncio.run(
        run_diagnosis(
            args.manifest,
            args.retrieval_report,
            args.chat_report,
            args.report_dir,
        )
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
