from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from eval.quality.metrics import RankedItem, normalize_url
from eval.quality.schema import GoldCase


@dataclass(frozen=True)
class GoldEvidence:
    variant_group: str
    url: str
    span: str | None


def normalize_evidence(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def load_gold_evidence(path: Path, cases: list[GoldCase]) -> dict[str, GoldEvidence]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_facts = payload.get("facts") if isinstance(payload, dict) else None
    if payload.get("version") != 1 or not isinstance(raw_facts, dict):
        raise ValueError(f"{path}: invalid evidence artifact")

    expected = {case.variant_group for case in cases}
    if set(raw_facts) != expected:
        raise ValueError(f"{path}: evidence fact ids do not match manifest")

    cases_by_group = {case.variant_group: case for case in cases}
    evidence: dict[str, GoldEvidence] = {}
    for group, raw in raw_facts.items():
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: {group} has invalid evidence")
        span = str(raw.get("span", "")).strip() or None
        if span is None and raw.get("status") != "CORPUS_EVIDENCE_MISSING":
            raise ValueError(f"{path}: {group} has an empty evidence span")
        url = str(raw.get("url", ""))
        gold_urls = {normalize_url(value) for value in cases_by_group[group].gold_urls}
        if normalize_url(url) not in gold_urls:
            raise ValueError(f"{path}: {group} evidence URL is outside gold URLs")
        evidence[group] = GoldEvidence(group, url, span)
    return evidence


def validate_evidence_texts(
    evidence: dict[str, GoldEvidence], document_texts: dict[str, str]
) -> None:
    normalized_documents = {
        normalize_url(url): normalize_evidence(text) for url, text in document_texts.items()
    }
    for group, gold in evidence.items():
        if gold.span is None:
            continue
        if normalize_evidence(gold.span) not in normalized_documents.get(
            normalize_url(gold.url), ""
        ):
            raise ValueError(f"{group}: evidence span is not present in indexed document")


def evidence_rank(gold: GoldEvidence, chunks: Iterable[RankedItem]) -> int | None:
    if gold.span is None:
        return None
    needle = normalize_evidence(gold.span)
    for rank, chunk in enumerate(chunks, start=1):
        if (
            chunk.url
            and normalize_url(chunk.url) == normalize_url(gold.url)
            and needle in normalize_evidence(chunk.text)
        ):
            return rank
    return None
