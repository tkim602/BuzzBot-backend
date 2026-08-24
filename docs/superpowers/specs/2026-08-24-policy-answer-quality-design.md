# Policy Answer Quality Design

## Goal

Isolate the Policy answer layer from retrieval variance, classify the accepted
dev-100 baseline's 21 answer-layer failures, and improve correctness without
weakening fail-closed validation or changing retrieval.

## Scope

- Freeze the accepted dev-100 production evidence (top five chunks per case)
  with report hash and git provenance.
- Preserve the existing 100 questions, gold answers, URLs, and metadata.
- Evaluate only `frozen evidence -> synthesis -> citation -> validation`.
- Record separate correctness, support, citation-present,
  citation-entails-claim, citation-source-correct, and abstention-correct
  metrics in LangSmith.
- Give each of the 21 existing class-C cases one primary taxonomy value:
  `SYNTHESIS_ERROR`, `INCOMPLETE_ANSWER`, `CITATION_MISMATCH`,
  `UNSUPPORTED_CLAIM`, `VALIDATOR_FALSE_REJECTION`,
  `UNNECESSARY_ABSTENTION`, `EVIDENCE_CONFLICT_HANDLING`, or
  `FORMATTING_CONTRACT_FAILURE`.
- After Policy passes, add a source-consistent 20-case Academic Calendar chat
  evaluation using the already frozen Calendar events.

## Architecture

The snapshot is a committed JSON data artifact, not a second retriever. A
small answer-only runner reconstructs the existing `RetrievedChunk` objects,
calls the existing answerer, and then calls the existing grounding, claim, and
polarity checks. It exposes both pre-validation output and final fail-closed
output so validator rejections are observable without changing production
responses.

LangSmith receives the question and frozen evidence as inputs, the gold answer
and source URLs as reference outputs, and the answer/citations/validator result
as outputs. One evaluator call returns the semantic answer and citation
metrics; deterministic evaluators handle citation presence, source URL, output
contract, and production validator state.

## Safety and gates

- No retrieval, ingestion, chunking, routing, or source-scope changes.
- No validation threshold changes and no removal of fail-closed behavior.
- No live full dev-100 run until the frozen-evidence candidate passes focused
  tests and the dominant failure hypothesis.
- Policy acceptance: correctness >= 70%, support >= 75%, citation grounding >=
  90%, unsupported confident answers = 0.
- Existing SQL, Schedule, NLU, Course Details, Calendar retrieval, unit, DB,
  and lint gates must not regress.
- Calendar answer quality is reported separately and becomes measurable; it
  does not alter the Policy acceptance result.

