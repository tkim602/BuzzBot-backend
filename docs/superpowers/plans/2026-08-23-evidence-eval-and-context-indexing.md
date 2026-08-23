# Evidence Evaluation and Contextual Indexing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure answer-supporting chunk retrieval accurately, then improve chunk indexing with local section context while preserving raw citation text and the current architecture.

**Architecture:** Add a committed dev-100 evidence-span artifact and deterministic Evidence Hit@K metrics beside existing URL metrics. Then replace document-wide chunk headings with local section paths and embed title + local path + raw body, using the existing chunking-version reindex mechanism.

**Tech Stack:** Python 3.12, PostgreSQL/pgvector, SQLAlchemy, existing OpenAI embedding client, existing cross-encoder and semantic verifier, pytest, Ruff.

---

### Task 1: Correct retrieval failure taxonomy

**Files:**
- Modify: `eval/quality/runner.py`
- Modify: `tests/test_quality_eval.py`

- [ ] **Step 1: Write failing taxonomy tests**

Add tests constructing production/raw/vector/FTS `CaseResult` rows and assert:

```python
assert "ALL_ABLATIONS_MISS" in production.failure_tags
assert "PRODUCTION_RECOVERS_ABLATIONS" in production.failure_tags
assert "ALL_METHODS_FAIL" not in production.failure_tags
```

Also assert a production miss receives `PRODUCTION_MISS`, while a production hit never does.

- [ ] **Step 2: Verify RED**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_quality_eval.py
```

Expected: failure because the current evaluator emits `ALL_METHODS_FAIL` and duplicates `raw` as `hybrid`.

- [ ] **Step 3: Implement the minimal tag correction**

In `_diagnose`, evaluate the three ablation modes exactly once:

```python
ablations = (raw, vector, fts)
if all(row is not None and not row.hit_at(5) for row in ablations):
    tags.append("ALL_ABLATIONS_MISS")
    if prod.hit_at(5):
        tags.append("PRODUCTION_RECOVERS_ABLATIONS")
if not prod.hit_at(5):
    tags.append("PRODUCTION_MISS")
```

Keep `GOLD_NOT_RETURNED`, `RANK_GT_5`, and existing recovery tags for report continuity.

- [ ] **Step 4: Verify GREEN and commit**

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_quality_eval.py
git add eval/quality/runner.py tests/test_quality_eval.py
git commit -m "fix: clarify retrieval failure taxonomy"
```

### Task 2: Add deterministic evidence-span evaluation

**Files:**
- Create: `eval/quality/evidence.py`
- Create: `eval/quality/build_evidence.py`
- Create: `eval/quality/gold_evidence/dev_100.json`
- Modify: `eval/quality/runner.py`
- Modify: `eval/quality/metrics.py`
- Modify: `tests/test_quality_eval.py`

- [ ] **Step 1: Write failing evidence artifact tests**

Define the artifact contract:

```json
{
  "version": 1,
  "manifest": "dev_100",
  "facts": {
    "gold-001": {
      "url": "https://registrar.gatech.edu/current-students/transcripts",
      "span": "exact text copied from the indexed official document"
    }
  }
}
```

Tests must reject missing fact IDs, empty spans, evidence URLs outside the case's gold URLs, and spans not found in the indexed gold document. Multiple facts may intentionally share one normalized URL. Matching normalizes whitespace only; it does not paraphrase.

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_quality_eval.py
```

Expected: import or assertion failure because `eval.quality.evidence` does not exist.

- [ ] **Step 3: Implement the artifact loader and matcher**

Create:

```python
@dataclass(frozen=True)
class GoldEvidence:
    variant_group: str
    url: str
    span: str

def normalize_evidence(text: str) -> str:
    return " ".join(text.split()).casefold()

def evidence_rank(gold: GoldEvidence, chunks: Iterable[RankedItem]) -> int | None:
    needle = normalize_evidence(gold.span)
    for rank, chunk in enumerate(chunks, 1):
        if normalize_url(chunk.url) == normalize_url(gold.url) and needle in normalize_evidence(chunk.text):
            return rank
    return None
```

Use existing URL normalization from `eval.quality.metrics`.

- [ ] **Step 4: Preserve retrieved chunk text in retrieval reports**

Extend `RankedItem` with `text: str | None = None`; populate it from `RetrievedChunk.chunk_text` and `DocumentEvidence.text`. Existing callers remain valid because the field has a default.

- [ ] **Step 5: Add evidence metrics beside URL metrics**

When `--evidence-file` is supplied, report:

```python
"evidence_hit_at_1"
"evidence_hit_at_3"
"evidence_hit_at_5"
"evidence_mrr_at_5"
```

Do not replace existing URL Hit@K.

- [ ] **Step 6: Build and validate the 100 fixed spans**

Start the existing BuzzBot database without deleting its volume:

```bash
docker compose start db
```

Implement `eval.quality.build_evidence` as a one-time, resumable CLI. For each fixed fact it loads the indexed document at a gold URL, splits it into decimal-safe sentence windows, ranks those windows against `gold_answer` with the installed cross-encoder, and checks candidates with the existing semantic verifier until one returns `SUPPORTED`. It writes only exact source text and resumes already completed fact IDs. Store the result in `eval/quality/gold_evidence/dev_100.json`. The final validation must query PostgreSQL and prove every committed span exists in its declared document.

Run:

```bash
PYTHONPATH=$PWD python3 -m eval.quality.build_evidence \
  --manifest eval/quality/manifests/dev_100.json \
  --output eval/quality/gold_evidence/dev_100.json
```

Expected artifact invariants:

```python
assert len(facts) == 100
assert set(facts) == {case.variant_group for case in dev_cases}
assert all(span_exists_in_declared_document(fact) for fact in facts.values())
```

- [ ] **Step 7: Record the pre-indexing evidence baseline**

```bash
PYTHONPATH=$PWD python3 -m eval.quality.runner \
  --manifest eval/quality/manifests/dev_100.json \
  --evidence-file eval/quality/gold_evidence/dev_100.json \
  --report-dir eval/quality/reports_evidence_baseline_dev_100
```

This is the frozen Evidence Hit@K baseline for Task 4. Do not change production code before it completes.

- [ ] **Step 8: Verify and commit**

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_quality_eval.py
git add eval/quality/evidence.py eval/quality/build_evidence.py \
  eval/quality/gold_evidence/dev_100.json \
  eval/quality/runner.py eval/quality/metrics.py tests/test_quality_eval.py
git commit -m "eval: measure answer-supporting chunk retrieval"
```

### Task 3: Store and embed local chunk context

**Files:**
- Modify: `ingestion/index.py`
- Modify: `ingestion/documents/sync.py`
- Modify: `tests/test_document_sync.py`
- Modify: `tests/integration/test_document_sync.py`

- [ ] **Step 1: Write failing indexing tests**

Create two chunks from different sections of one document and assert:

```python
assert stored_chunks[0].headings == "Admissions > Recommendations"
assert stored_chunks[1].headings == "Admissions > Deadlines"
assert embedded_texts[0].startswith("Page title\nAdmissions > Recommendations\n")
assert embedded_texts[1].startswith("Page title\nAdmissions > Deadlines\n")
assert stored_chunks[0].chunk_text == original_raw_text
```

The test must prove the entire document heading list is not copied onto every chunk.

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=$PWD python3 -m pytest -q \
  tests/test_document_sync.py tests/integration/test_document_sync.py
```

Expected: stored headings are document-wide and embeddings receive raw body only.

- [ ] **Step 3: Implement contextual embedding text**

In `index_chunks`, derive per-chunk headings from metadata and keep raw body separate:

```python
local_heading = str(c.metadata.get("page_section_path") or "").strip() or None
chunk_obj = Chunk(..., headings=local_heading, chunk_text=c.text, ...)
```

Embed:

```python
def _embedding_text(chunk: Chunk) -> str:
    return "\n".join(value for value in (chunk.title, chunk.headings, chunk.chunk_text) if value)
```

Do not change `chunk_text`, so exact citation grounding remains unchanged.

- [ ] **Step 4: Increment the chunking version**

Change:

```python
CHUNKING_VERSION = 3
```

Keep `_uses_current_chunking` and the existing transactional reindex path unchanged.

- [ ] **Step 5: Verify GREEN and commit**

```bash
PYTHONPATH=$PWD python3 -m pytest -q \
  tests/test_document_sync.py tests/integration/test_document_sync.py
git add ingestion/index.py ingestion/documents/sync.py \
  tests/test_document_sync.py tests/integration/test_document_sync.py
git commit -m "fix: index chunks with local section context"
```

### Task 4: Reindex safely and establish the evidence baseline

**Files:**
- Runtime reports only: `eval/quality/reports_evidence_context_dev_100/`
- Create: `eval/quality/reindex_gold.py`
- Test: `tests/test_quality_reindex.py`

- [ ] **Step 1: Write the failing reindex orchestration test**

Mock `sync_document_url` and assert the orchestrator deduplicates repeated fact variants, resolves each registered source, calls each unique URL once, and preserves failures in its summary without deleting database rows.

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_quality_reindex.py
```

Expected: import failure because `eval.quality.reindex_gold` does not exist.

- [ ] **Step 3: Implement, verify, and commit the thin orchestrator**

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_quality_reindex.py
git add eval/quality/reindex_gold.py tests/test_quality_reindex.py
git commit -m "eval: safely reindex fixed gold documents"
```

- [ ] **Step 4: Verify budget and database readiness**

```bash
make usage
make migrate
```

Expected: usage below `$3`; migrations succeed.

- [ ] **Step 5: Reindex only documents referenced by dev-100 gold URLs**

Implement `eval.quality.reindex_gold` as a thin orchestrator over existing `sync_document_url`: load unique `(gold_source, gold_url)` pairs from the manifest, select the registered source, run each URL sequentially, and print indexed, unchanged, and failed counts. It must not delete documents or chunks manually. A URL is replaced only after existing fetch, chunk, and embedding transactions succeed.

Run:

```bash
PYTHONPATH=$PWD python3 -m eval.quality.reindex_gold \
  --manifest eval/quality/manifests/dev_100.json
```

- [ ] **Step 6: Run retrieval dev-100 with evidence metrics**

```bash
PYTHONPATH=$PWD python3 -m eval.quality.runner \
  --manifest eval/quality/manifests/dev_100.json \
  --evidence-file eval/quality/gold_evidence/dev_100.json \
  --report-dir eval/quality/reports_evidence_context_dev_100
```

- [ ] **Step 7: Apply the gate**

Compare against the current URL baseline (`Hit@5=57%`, `MRR@5=0.400`). Accept contextual indexing only when:

```text
Evidence Hit@5 improves over the pre-reindex evidence baseline
URL Hit@5 >= 57%
URL MRR@5 >= 0.400
wins > regressions
```

If it fails, stop. Do not change chunk size or add another retrieval heuristic.

### Task 5: Final automated verification

**Files:**
- No new production files.

- [ ] **Step 1: Run all checks**

```bash
PYTHONPATH=$PWD python3 -m pytest -q
RUN_DB_TESTS=1 PYTHONPATH=$PWD python3 -m pytest -q tests/integration
ruff check .
ruff format --check .
```

- [ ] **Step 2: Report the next single bottleneck**

Use evidence-level case results to choose exactly one next plan:

- evidence missing from candidates → sibling-chunk retrieval;
- evidence retrieved but chat abstains/fails → evidence handoff/answer utilization;
- both materially large → choose the larger bucket first.

Do not run chat dev-100 in this plan. It is reserved for the next accepted production milestone.
