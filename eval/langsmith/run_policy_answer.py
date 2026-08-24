from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

TAXONOMY_LABELS = {
    "SYNTHESIS_ERROR",
    "INCOMPLETE_ANSWER",
    "CITATION_MISMATCH",
    "UNSUPPORTED_CLAIM",
    "VALIDATOR_FALSE_REJECTION",
    "UNNECESSARY_ABSTENTION",
    "EVIDENCE_CONFLICT_HANDLING",
    "FORMATTING_CONTRACT_FAILURE",
}


@dataclass(frozen=True)
class PolicyCase:
    case_id: str
    question: str
    gold_answer: str
    gold_urls: tuple[str, ...]
    evidence: tuple[dict[str, object], ...]
    metadata: dict[str, object]


@dataclass(frozen=True)
class PolicySnapshot:
    provenance: dict[str, str]
    cases: tuple[PolicyCase, ...]


@dataclass(frozen=True)
class TaxonomyRow:
    case_id: str
    category: str
    rationale: str


def load_snapshot(path: Path) -> PolicySnapshot:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = tuple(
        PolicyCase(
            case_id=str(item["case_id"]),
            question=str(item["question"]),
            gold_answer=str(item["gold_answer"]),
            gold_urls=tuple(str(url) for url in item["gold_urls"]),
            evidence=tuple(dict(evidence) for evidence in item["evidence"]),
            metadata=dict(item["metadata"]),
        )
        for item in payload["cases"]
    )
    if len(cases) != 100 or len({case.case_id for case in cases}) != 100:
        raise ValueError("policy-answer-dev-100-v1 must contain 100 unique cases")
    return PolicySnapshot(provenance=dict(payload["provenance"]), cases=cases)


def load_taxonomy(path: Path) -> tuple[TaxonomyRow, ...]:
    rows = tuple(
        TaxonomyRow(
            case_id=str(item["case_id"]),
            category=str(item["category"]),
            rationale=str(item["rationale"]),
        )
        for item in json.loads(path.read_text(encoding="utf-8"))["items"]
    )
    if any(row.category not in TAXONOMY_LABELS for row in rows):
        raise ValueError("unknown policy answer taxonomy label")
    return rows

