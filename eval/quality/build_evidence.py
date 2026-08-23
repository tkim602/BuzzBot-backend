from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.rag.grounding import semantic_claim_verdict
from db.models import Document
from db.session import SyncSessionLocal
from eval.quality.evidence import load_gold_evidence, validate_evidence_texts
from eval.quality.metrics import normalize_url
from eval.quality.schema import GoldCase, load_manifest_cases

_SENTENCE_BOUNDARY = re.compile(r"(?<=[!?])\s+|(?<!\d)(?<=\.)\s+|\n+")


def _windows(text: str) -> list[str]:
    sentences = [part.strip() for part in _SENTENCE_BOUNDARY.split(text) if part.strip()]
    windows: list[str] = []
    for index in range(len(sentences)):
        for size in (1, 2, 3):
            window = "\n".join(sentences[index : index + size])
            if window and len(window) <= 1800:
                windows.append(window)
    return list(dict.fromkeys(windows))


def _rank(model, case: GoldCase, candidates: list[str]) -> list[str]:
    query = f"{case.gold_answer}\n{case.gold_locator}"
    scores = model.predict([(query, candidate) for candidate in candidates])
    return [
        candidate for _, candidate in sorted(zip(scores, candidates, strict=True), reverse=True)
    ]


async def _select(model, case: GoldCase, text: str) -> str | None:
    for candidate in _rank(model, case, _windows(text))[:20]:
        if await semantic_claim_verdict(case.gold_answer, candidate) == "SUPPORTED":
            return candidate
    return None


async def build(manifest: Path, output: Path) -> None:
    from sentence_transformers import CrossEncoder

    cases = load_manifest_cases(manifest)
    model = CrossEncoder(settings.rag_rerank_model)
    with SyncSessionLocal() as session:
        documents = {
            normalize_url(document.canonical_url): document.content_text or ""
            for document in session.scalars(select(Document)).all()
        }

    payload = {"version": 1, "manifest": manifest.stem, "facts": {}}
    if output.exists():
        payload = json.loads(output.read_text(encoding="utf-8"))
    facts = payload.setdefault("facts", {})
    output.parent.mkdir(parents=True, exist_ok=True)

    for case in cases:
        if case.variant_group in facts:
            continue
        url = next((url for url in case.gold_urls if normalize_url(url) in documents), None)
        if url is None:
            raise RuntimeError(f"{case.variant_group}: indexed gold document not found")
        span = await _select(model, case, documents[normalize_url(url)])
        facts[case.variant_group] = (
            {"url": url, "span": span}
            if span
            else {"url": url, "status": "CORPUS_EVIDENCE_MISSING"}
        )
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{case.variant_group}: {'selected' if span else 'corpus evidence missing'}")

    evidence = load_gold_evidence(output, cases)
    validate_evidence_texts(evidence, documents)
    print(f"validated={len(evidence)} output={output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exact evidence spans for a fixed manifest")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(build(args.manifest, args.output))


if __name__ == "__main__":
    main()
