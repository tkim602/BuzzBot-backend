# RAG Improvement Study Guide for BuzzBot

> A research-backed guide to improving retrieval and generation quality in BuzzBot, Georgia Tech's RAG chatbot.

---

## Table of Contents

1. [BuzzBot Current Architecture](#buzzbot-current-architecture)
2. [Advanced Chunking](#1-advanced-chunking)
3. [Query Transformation](#2-query-transformation)
4. [Advanced Retrieval Models](#3-advanced-retrieval-models)
5. [Reranking](#4-reranking)
6. [Context Compression](#5-context-compression)
7. [Better Embedding Models](#6-better-embedding-models)
8. [Evaluation Frameworks](#7-evaluation-frameworks)
9. [Agentic RAG](#8-agentic-rag)
10. [Fine-Tuning for RAG](#9-fine-tuning-for-rag)
11. [Knowledge Graph RAG](#10-knowledge-graph-rag)
12. [Prompt Engineering](#11-prompt-engineering)
13. [Prioritized Recommendations](#prioritized-recommendations)
14. [References](#references)

---

## BuzzBot Current Architecture

A quick reference for how BuzzBot works today, so each technique's applicability is clear.

| Component | Current Implementation |
|---|---|
| **Embedding model** | OpenAI `text-embedding-3-small` (1536-dim) |
| **Vector store** | PostgreSQL + pgvector (cosine similarity) |
| **Chunking** | Token-aware, 500 tokens / 80 overlap, splits on markdown headings |
| **Retrieval** | Hybrid: pgvector top-8 + PostgreSQL FTS top-5, fused via RRF (k=60) |
| **Query rewrite** | Rule-based temporal grounding + optional LLM rewrite for pronouns/follow-ups |
| **Router** | Intent classifier → freshness strategy (indexed / live_fetch / hybrid) |
| **LLM** | GPT-4o-mini (temp 0.2, max 1500 tokens, strict JSON output) |
| **Grounding** | Post-hoc citation verification via substring match / word-overlap ≥50% |
| **Data sources** | GT Registrar, Catalog, Library, Scheduler (sitemap-driven ingestion) |

### Key Pain Points

- **Retrieval misses**: correct documents not surfaced for some queries
- **Wrong answers from correct sources**: LLM hallucinates or misreads retrieved chunks
- **No systematic evaluation**: hard to measure whether changes actually help

---

## 1. Advanced Chunking

BuzzBot currently uses fixed-size token windows with heading-based splits. Smarter chunking can dramatically improve what gets retrieved.

### 1.1 Proposition-Based Chunking (Dense X Retrieval)

| | |
|---|---|
| **Paper** | *Dense X Retrieval: What Retrieval Granularity Should We Use?* — Tong Chen et al., 2023 |
| **Link** | [arXiv:2312.06648](https://arxiv.org/abs/2312.06648) |
| **Complexity** | Medium |
| **Priority** | Near-term |

**Core idea.** Instead of chunking by token count, decompose documents into atomic *propositions* — self-contained factual statements. Each proposition becomes its own retrieval unit. An LLM extracts propositions like "CS 1332 is a 3-credit course offered every semester" from a longer paragraph.

**BuzzBot application.** GT catalog pages pack many facts into dense paragraphs (prerequisites, credit hours, offered terms). A single 500-token chunk might contain facts about 3 different courses, diluting the embedding. Proposition-level indexing would let BuzzBot retrieve the exact fact needed. Implementation: add an LLM-based proposition extraction step in `ingestion/chunk.py` after content extraction, store propositions as chunks with a pointer back to the parent document.

### 1.2 Parent-Child (Small-to-Big) Retrieval

| | |
|---|---|
| **Paper** | Popularized by LlamaIndex documentation; related to *Sentence Window Retrieval* patterns |
| **Link** | [LlamaIndex docs](https://docs.llamaindex.ai/en/stable/examples/node_postprocessor/MetadataReplacementDemo/) |
| **Complexity** | Low-Medium |
| **Priority** | Immediate |

**Core idea.** Index small chunks (e.g., 128 tokens) for precise matching, but at generation time expand to the parent chunk (e.g., 1024 tokens) to give the LLM enough context. This separates retrieval granularity from generation context.

**BuzzBot application.** Currently, the 500-token chunk is a compromise — small enough for decent retrieval, large enough for context. With parent-child, you could index at 128 tokens for sharper embeddings while returning the full section at generation time. Implementation: add a `parent_chunk_id` column to the `chunks` table, index child chunks in `embeddings`, and expand to parents before passing to the LLM in `app/rag/retrieval.py`.

### 1.3 RAPTOR — Recursive Abstractive Processing for Tree-Organized Retrieval

| | |
|---|---|
| **Paper** | *RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval* — Sarthi et al., 2024 |
| **Link** | [arXiv:2401.18059](https://arxiv.org/abs/2401.18059) |
| **Complexity** | High |
| **Priority** | Longer-term |

**Core idea.** Build a tree of summaries over chunks. Leaf nodes are original chunks; higher levels are LLM-generated summaries of clusters. Retrieval can match at any level — specific details at leaves, thematic queries at higher nodes.

**BuzzBot application.** Useful for broad questions like "What are the CS degree requirements?" that span many pages. A RAPTOR tree over GT Catalog content would let BuzzBot retrieve a pre-summarized overview rather than hoping to find the right individual chunks. High implementation cost: requires clustering, recursive summarization, and tree-traversal retrieval logic.

### 1.4 Semantic Chunking

| | |
|---|---|
| **Paper** | Described in Greg Kamradt's work and LlamaIndex's `SemanticSplitterNodeParser` |
| **Link** | [LlamaIndex Semantic Chunking](https://docs.llamaindex.ai/en/stable/examples/node_postprocessor/MetadataReplacementDemo/) |
| **Complexity** | Low |
| **Priority** | Immediate |

**Core idea.** Instead of fixed token windows, compute sentence-level embeddings and split where cosine similarity between consecutive sentences drops below a threshold. This produces chunks that are semantically coherent rather than arbitrarily cut.

**BuzzBot application.** BuzzBot's heading-based splitting is already better than naive fixed-window, but pages without clear headings (e.g., policy pages, FAQs) still get split mid-topic. Semantic chunking would handle these cases gracefully. Drop-in replacement in `ingestion/chunk.py` using the existing embedding function to compute sentence similarities.

---

## 2. Query Transformation

BuzzBot has basic query rewriting (temporal grounding, pronoun resolution). These techniques go further.

### 2.1 HyDE — Hypothetical Document Embeddings

| | |
|---|---|
| **Paper** | *Precise Zero-Shot Dense Retrieval without Relevance Labels* — Gao et al., 2022 |
| **Link** | [arXiv:2212.10496](https://arxiv.org/abs/2212.10496) |
| **Complexity** | Low |
| **Priority** | Immediate |

**Core idea.** Instead of embedding the user's query directly, first ask the LLM to generate a *hypothetical answer*, then embed that. The hypothesis lives in the same semantic space as real documents, bridging the query-document vocabulary gap.

**BuzzBot application.** When a student asks "When do I register for classes?", the query embedding is far from the registrar page that says "Phase I registration for Fall 2025 begins March 17." A HyDE-generated hypothesis like "Registration for the upcoming semester typically begins in mid-March…" would embed much closer to the actual document. Implementation: add a HyDE step in `app/rag/query_rewrite.py` before embedding — one extra LLM call per query (use GPT-4o-mini, ~$0.0001/query).

### 2.2 Multi-Query Retrieval

| | |
|---|---|
| **Paper** | Described in RAG-Fusion — *Forget RAG, the Future is RAG-Fusion* — Rackauckas, 2023 |
| **Link** | [arXiv:2402.03367](https://arxiv.org/abs/2402.03367) |
| **Complexity** | Low |
| **Priority** | Immediate |

**Core idea.** Generate multiple reformulations of the user's query, retrieve for each, and fuse results (typically via RRF). Different phrasings activate different relevant documents.

**BuzzBot application.** BuzzBot already uses RRF for vector+FTS fusion, so the infrastructure is there. Adding multi-query means generating 3-4 rephrasings in `query_rewrite.py`, running retrieval for each, and extending the existing RRF logic in `app/rag/retrieval.py` to fuse across all result sets. Particularly useful for ambiguous queries like "CS electives" which could mean technical electives, free electives, or threads.

### 2.3 Step-Back Prompting

| | |
|---|---|
| **Paper** | *Take a Step Back: Evoking Reasoning via Abstraction in Large Language Models* — Zheng et al., 2023 |
| **Link** | [arXiv:2310.06117](https://arxiv.org/abs/2310.06117) |
| **Complexity** | Low |
| **Priority** | Near-term |

**Core idea.** Before answering, ask the LLM to generate a more abstract "step-back" question. For "What are the prerequisites for CS 3510?", the step-back might be "What is the prerequisite structure for upper-level CS courses at Georgia Tech?" Retrieve for both, giving the LLM broader context.

**BuzzBot application.** Helps with questions that require understanding GT's overall structure. Could be implemented as an optional retrieval mode triggered by the router when the intent is `catalog_course` or `general`. Low cost — one extra LLM call + retrieval pass.

### 2.4 Query Decomposition

| | |
|---|---|
| **Paper** | *Demonstrate-Search-Predict: Composing retrieval and language model pipelines* — Khattab et al., 2022 |
| **Link** | [arXiv:2212.14024](https://arxiv.org/abs/2212.14024) |
| **Complexity** | Medium |
| **Priority** | Near-term |

**Core idea.** Break complex queries into sub-questions, retrieve and answer each independently, then synthesize. "Compare the CS and CM programs" → "What are CS degree requirements?" + "What are CM degree requirements?"

**BuzzBot application.** Students frequently ask comparison or multi-part questions. The router could detect these and trigger decomposition. Each sub-question goes through the full RAG pipeline, and a final synthesis step merges answers. Adds latency (sequential sub-queries) but significantly improves accuracy for complex questions.

---

## 3. Advanced Retrieval Models

BuzzBot uses standard dense retrieval (bi-encoder) + lexical FTS. These models offer richer matching.

### 3.1 ColBERT — Late Interaction Retrieval

| | |
|---|---|
| **Paper** | *ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT* — Khattab & Zaharia, 2020 |
| **Link** | [arXiv:2004.12832](https://arxiv.org/abs/2004.12832) |
| **Complexity** | High |
| **Priority** | Longer-term |

**Core idea.** Instead of compressing query and document into single vectors, ColBERT keeps per-token embeddings and computes a MaxSim (maximum similarity) score between all query-token and document-token pairs. This captures fine-grained token-level matching while remaining efficient via pre-computed document embeddings.

**BuzzBot application.** ColBERT would significantly improve retrieval for queries with specific terms (course codes, instructor names, building names) where a single-vector embedding might not capture the important token. However, it requires replacing pgvector with a ColBERT-specific index (e.g., using `colbert-ai/colbertv2.0` with PLAID indexing or RAGatouille). This is a larger infrastructure change.

### 3.2 SPLADE — Sparse Lexical and Dense Retrieval

| | |
|---|---|
| **Paper** | *SPLADE v2: Sparse Lexical and Expansion Model for Information Retrieval* — Formal et al., 2021 |
| **Link** | [arXiv:2109.10086](https://arxiv.org/abs/2109.10086) |
| **Complexity** | Medium |
| **Priority** | Near-term |

**Core idea.** SPLADE learns sparse representations where each dimension corresponds to a vocabulary term. It performs learned term expansion — the model predicts which terms *should* appear in the document/query even if they don't literally appear. Combines the interpretability of sparse retrieval with the semantic understanding of neural models.

**BuzzBot application.** SPLADE could replace or augment BuzzBot's PostgreSQL FTS component. It would handle vocabulary mismatch better than raw tsvector (e.g., "sign up for classes" → "registration"). Can be served via Elasticsearch with ELSER or as a standalone model. A practical middle ground between the current FTS and full ColBERT.

---

## 4. Reranking

Reranking is one of the highest-impact, lowest-effort improvements. BuzzBot currently has no reranker — RRF output goes directly to the LLM.

### 4.1 Cross-Encoder Reranking

| | |
|---|---|
| **Paper** | *Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks* — Reimers & Gurevych, 2019 (cross-encoder section) |
| **Link** | [arXiv:1908.10084](https://arxiv.org/abs/1908.10084) |
| **Models** | `BAAI/bge-reranker-v2-m3`, `cross-encoder/ms-marco-MiniLM-L-6-v2`, Cohere `rerank-english-v3.0` |
| **Complexity** | Low |
| **Priority** | Immediate |

**Core idea.** A cross-encoder takes (query, document) pairs as joint input and produces a relevance score. Unlike bi-encoders that embed query and document independently, cross-encoders can attend across both, yielding much higher accuracy. Too slow for first-stage retrieval (must score every document), but perfect for reranking a small candidate set.

**BuzzBot application.** After RRF fusion produces ~10-13 candidates, pass them through a cross-encoder to reorder by true relevance. This is the single highest-impact change for retrieval quality. Implementation options:
- **Cohere Rerank API** (`rerank-english-v3.0`): 2 lines of code, ~$0.001/query, 200ms latency. Drop into `app/rag/retrieval.py` after RRF.
- **Local model** (`bge-reranker-v2-m3`): Free, ~100ms on GPU, ~500ms on CPU. Use `sentence-transformers` CrossEncoder class.
- Insert between RRF fusion and LLM call — filter to top-5 after reranking.

### 4.2 LLM-as-Reranker

| | |
|---|---|
| **Paper** | *Is ChatGPT Good at Search? Investigating Large Language Models as Re-Ranking Agents* — Sun et al., 2023 |
| **Link** | [arXiv:2304.09542](https://arxiv.org/abs/2304.09542) |
| **Complexity** | Low |
| **Priority** | Near-term |

**Core idea.** Use the LLM itself to rerank candidates by prompting it with the query and candidate passages, asking it to order them by relevance. Surprisingly effective — GPT-4 reranking matches or beats supervised cross-encoders on some benchmarks.

**BuzzBot application.** Since BuzzBot already calls GPT-4o-mini, you could add a reranking prompt before the answering prompt. Trade-off: adds one LLM call (~$0.0003 with GPT-4o-mini, ~300ms). Less accurate than a dedicated cross-encoder but requires zero new infrastructure. Good as a quick experiment before committing to a cross-encoder.

---

## 5. Context Compression

After retrieval, the context sent to the LLM may contain irrelevant sentences. Compression removes noise.

### 5.1 LongLLMLingua

| | |
|---|---|
| **Paper** | *LongLLMLingua: Accelerating and Enhancing LLMs in Long Context Scenarios via Prompt Compression* — Jiang et al., 2023 |
| **Link** | [arXiv:2310.06839](https://arxiv.org/abs/2310.06839) |
| **Complexity** | Medium |
| **Priority** | Near-term |

**Core idea.** Uses a small LLM (e.g., LLaMA-2-7B) to score each token's importance given the query, then drops low-importance tokens, achieving 2-10x compression with minimal quality loss. Particularly effective for RAG where retrieved passages have varying relevance.

**BuzzBot application.** When BuzzBot retrieves 8 chunks (8 × 500 = 4000 tokens of context), much of it may be tangential. Compressing to the most relevant 1500 tokens would reduce noise and cost. Implementation: add a compression step between retrieval and LLM call. Requires running a small local model or using the LLMLingua library. More relevant if BuzzBot scales to larger context windows.

### 5.2 FILCO — Fine-Grained Context Filtering

| | |
|---|---|
| **Paper** | *Learning to Filter Context for Retrieval-Augmented Generation* — Wang et al., 2023 |
| **Link** | [arXiv:2311.08377](https://arxiv.org/abs/2311.08377) |
| **Complexity** | Medium |
| **Priority** | Longer-term |

**Core idea.** Train a classifier to predict which sentences in a retrieved passage are useful for answering the query, then only include those sentences in the prompt. Unlike token-level compression, FILCO operates at the sentence level for more interpretable filtering.

**BuzzBot application.** Could be approximated without training by using the LLM to extract relevant sentences from each chunk before the final answer generation. A lightweight version: split each chunk into sentences, embed them, and only keep sentences above a similarity threshold to the query. This is simpler than full FILCO but captures the core benefit.

---

## 6. Better Embedding Models

BuzzBot uses `text-embedding-3-small`. Upgrading the embedding model is a straightforward improvement.

### 6.1 OpenAI text-embedding-3-large

| | |
|---|---|
| **Provider** | OpenAI |
| **Dimensions** | 3072 (or truncated to 256/1024 via Matryoshka) |
| **Complexity** | Low |
| **Priority** | Immediate |

**Core idea.** Direct upgrade from `text-embedding-3-small`. ~3% better on MTEB benchmarks. Supports Matryoshka representation learning — you can truncate to 1024 dims and still outperform the small model, saving storage.

**BuzzBot application.** Change one string in `app/core/config.py` and re-embed. Cost doubles ($0.00013 → $0.00013/1K tokens for large), but BuzzBot's corpus is small enough that this is negligible. Use 1024-dim Matryoshka to keep pgvector index size manageable while getting better quality. Requires re-indexing all chunks.

### 6.2 Jina Embeddings v3

| | |
|---|---|
| **Provider** | Jina AI |
| **Paper** | *Jina Embeddings: A Novel Set of High-Performance Sentence Embedding Models* |
| **Link** | [arXiv:2307.11224](https://arxiv.org/abs/2307.11224) |
| **Complexity** | Low |
| **Priority** | Near-term |

**Core idea.** Task-specific LoRA adapters for different use cases (retrieval, classification, etc.). Supports up to 8192-token inputs, multilingual, and competitive with OpenAI on MTEB. Offers asymmetric embeddings — different encoding for queries vs. documents.

**BuzzBot application.** The 8192-token context window is attractive — BuzzBot could embed larger chunks or even full pages, reducing chunking-related information loss. Asymmetric query/document encoding could improve retrieval quality. Available via API or self-hosted.

### 6.3 BGE-M3

| | |
|---|---|
| **Paper** | *BGE M3-Embedding: Multi-Lingual, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation* — Chen et al., 2024 |
| **Link** | [arXiv:2402.03216](https://arxiv.org/abs/2402.03216) |
| **Complexity** | Medium |
| **Priority** | Near-term |

**Core idea.** A single model that produces dense, sparse (SPLADE-like), and ColBERT-style multi-vector embeddings simultaneously. Supports hybrid retrieval natively — no need for separate FTS infrastructure.

**BuzzBot application.** BGE-M3 could replace both the embedding model and PostgreSQL FTS in one shot, since it produces both dense and sparse representations. The sparse component would be a learned replacement for tsvector. Requires self-hosting (or using HuggingFace Inference Endpoints) and updating the retrieval pipeline to use its multi-vector outputs.

### 6.4 Cohere embed-v3

| | |
|---|---|
| **Provider** | Cohere |
| **Complexity** | Low |
| **Priority** | Near-term |

**Core idea.** Compression-aware embeddings with int8/binary quantization support, input type differentiation (search_document vs. search_query), and strong MTEB performance. Binary embeddings enable 32x storage reduction with minimal quality loss.

**BuzzBot application.** The input type parameter (document vs. query) gives the model explicit information about the embedding's purpose, similar to asymmetric training. Binary quantization would let BuzzBot scale to much larger corpora without pgvector index bloat. API-based — easy integration.

---

## 7. Evaluation Frameworks

BuzzBot has no systematic evaluation. You can't improve what you can't measure.

### 7.1 RAGAS

| | |
|---|---|
| **Paper** | *RAGAS: Automated Evaluation of Retrieval Augmented Generation* — Es et al., 2023 |
| **Link** | [arXiv:2309.15217](https://arxiv.org/abs/2309.15217) |
| **Complexity** | Low |
| **Priority** | Immediate |

**Core idea.** Evaluates RAG pipelines on four dimensions using LLM-as-judge:
- **Faithfulness**: Is the answer grounded in the retrieved context?
- **Answer relevancy**: Does the answer address the question?
- **Context precision**: Are the retrieved documents relevant?
- **Context recall**: Were all necessary documents retrieved?

**BuzzBot application.** Critical first step. Build a test set of 50-100 (question, ground-truth answer, source URL) triples from real student questions. Run RAGAS after every pipeline change to track improvement. Implementation: `pip install ragas`, create eval script in `tests/`, integrate into CI. RAGAS will immediately quantify BuzzBot's retrieval and generation quality, guiding which other improvements to prioritize.

### 7.2 RAGChecker

| | |
|---|---|
| **Paper** | *RAGChecker: A Fine-grained Framework for Diagnosing Retrieval-Augmented Generation* — Ru et al., 2024 |
| **Link** | [arXiv:2408.08067](https://arxiv.org/abs/2408.08067) |
| **Complexity** | Medium |
| **Priority** | Near-term |

**Core idea.** Goes beyond RAGAS with claim-level evaluation. Decomposes answers into atomic claims and checks each against retrieved context and ground truth. Provides fine-grained metrics: noise sensitivity, hallucination rate, information completeness.

**BuzzBot application.** Once RAGAS is in place and giving you aggregate scores, RAGChecker can diagnose *why* scores are low. For example, it can tell you whether BuzzBot's errors are due to missing retrieval (context recall) or hallucination from noisy context (noise sensitivity). Use this for deeper debugging.

### 7.3 TruLens

| | |
|---|---|
| **Paper** | Open-source library by TruEra |
| **Link** | [github.com/truera/trulens](https://github.com/truera/trulens) |
| **Complexity** | Low-Medium |
| **Priority** | Near-term |

**Core idea.** Production monitoring for RAG apps. Logs every query, retrieval, and response, then evaluates quality metrics (groundedness, relevance, toxicity) on each interaction. Provides a dashboard for ongoing monitoring.

**BuzzBot application.** While RAGAS evaluates offline test sets, TruLens monitors production quality in real time. Log every `/chat` request with retrieved chunks and response, evaluate asynchronously, and surface degradations. Useful for catching issues as new data is ingested or the model changes.

---

## 8. Agentic RAG

These approaches let the system decide *when* and *how* to retrieve, rather than always following the same pipeline.

### 8.1 Self-RAG

| | |
|---|---|
| **Paper** | *Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection* — Asai et al., 2023 |
| **Link** | [arXiv:2310.11511](https://arxiv.org/abs/2310.11511) |
| **Complexity** | High |
| **Priority** | Longer-term |

**Core idea.** The LLM generates special reflection tokens that decide: (1) whether retrieval is needed, (2) whether retrieved passages are relevant, (3) whether the generated response is supported by the passages. The model self-critiques and can re-retrieve if needed.

**BuzzBot application.** BuzzBot's grounding check is a simplified version of Self-RAG's critique step. Full Self-RAG would require fine-tuning or using a model trained with reflection tokens (available from the authors). A lightweight approximation: after generating an answer, check confidence — if low, reformulate the query and re-retrieve before responding. This can be implemented in the existing pipeline as a retry loop in the `/chat` endpoint.

### 8.2 CRAG — Corrective RAG

| | |
|---|---|
| **Paper** | *Corrective Retrieval Augmented Generation* — Yan et al., 2024 |
| **Link** | [arXiv:2401.15884](https://arxiv.org/abs/2401.15884) |
| **Complexity** | Medium |
| **Priority** | Near-term |

**Core idea.** After retrieval, evaluate document quality. If documents are relevant → proceed. If ambiguous → refine query and re-retrieve. If irrelevant → fall back to web search. Introduces a lightweight retrieval evaluator that acts as a gatekeeper.

**BuzzBot application.** Maps well onto BuzzBot's existing architecture. The router already classifies intent and can trigger live_fetch as a fallback. CRAG would add a retrieval quality check: after RRF, score the top result's relevance (using a cross-encoder or LLM). If below threshold, either re-query with a reformulated query or trigger live_fetch. This directly addresses the "retrieval miss" problem.

### 8.3 Adaptive RAG

| | |
|---|---|
| **Paper** | *Adaptive-RAG: Learning to Adapt Retrieval-Augmented Large Language Models through Question Complexity* — Jeong et al., 2024 |
| **Link** | [arXiv:2403.14403](https://arxiv.org/abs/2403.14403) |
| **Complexity** | Medium |
| **Priority** | Near-term |

**Core idea.** Route queries to different strategies based on complexity. Simple factual questions → single retrieval. Multi-hop questions → iterative retrieval. Questions the LLM can answer directly → no retrieval needed.

**BuzzBot application.** BuzzBot's router already does intent-based routing. Adaptive RAG would extend this with complexity-based routing: "What is CS 1332?" → simple retrieval, "Compare the Devices and Intelligence threads" → multi-hop with decomposition, "What is Georgia Tech?" → direct LLM answer (no retrieval needed). Reduces latency for simple queries while improving accuracy for complex ones.

---

## 9. Fine-Tuning for RAG

### 9.1 RAFT — Retrieval Augmented Fine Tuning

| | |
|---|---|
| **Paper** | *RAFT: Adapting Language Model to Domain Specific RAG* — Zhang et al., 2024 |
| **Link** | [arXiv:2403.10131](https://arxiv.org/abs/2403.10131) |
| **Complexity** | High |
| **Priority** | Longer-term |

**Core idea.** Fine-tune the LLM on a mix of (question, relevant doc + distractor docs, answer) examples. The model learns to identify the relevant document among distractors and extract the answer with citations. Trained with chain-of-thought to show reasoning.

**BuzzBot application.** Fine-tuning GPT-4o-mini on BuzzBot's specific domain (GT registrar data, catalog, schedules) could dramatically improve answer quality. The training data format matches BuzzBot's existing pipeline output: question + retrieved chunks (some relevant, some not) + expected answer with citations. Requires building a training set of 500-1000 examples. Cost: ~$5-10 for fine-tuning GPT-4o-mini.

### 9.2 Embedding Fine-Tuning

| | |
|---|---|
| **Paper** | *Improving Text Embeddings with Large Language Models* — Wang et al., 2024 |
| **Link** | [arXiv:2401.00368](https://arxiv.org/abs/2401.00368) |
| **Complexity** | Medium |
| **Priority** | Longer-term |

**Core idea.** Fine-tune the embedding model on domain-specific (query, positive passage, negative passage) triples. The model learns which documents should be close to which queries in your specific domain.

**BuzzBot application.** Generate training triples from BuzzBot's logs: (student question, chunk that answered it correctly, chunks that were retrieved but unhelpful). Use OpenAI's fine-tuning API for `text-embedding-3-small` or fine-tune an open model like `bge-base-en-v1.5` with `sentence-transformers`. Requires collecting real usage data first — implement logging and RAGAS evaluation before attempting this.

---

## 10. Knowledge Graph RAG

### 10.1 GraphRAG (Microsoft)

| | |
|---|---|
| **Paper** | *From Local to Global: A Graph RAG Approach to Query-Focused Summarization* — Edge et al., 2024 |
| **Link** | [arXiv:2404.16130](https://arxiv.org/abs/2404.16130) |
| **Complexity** | High |
| **Priority** | Longer-term |

**Core idea.** Build a knowledge graph from documents (entities + relationships), then use community detection to create hierarchical summaries. For global queries ("What are all the CS threads?"), traverse the graph to find connected entities. For local queries, combine graph traversal with vector retrieval.

**BuzzBot application.** GT's academic structure is inherently graph-shaped: courses have prerequisites, belong to programs, are taught by instructors, offered in semesters. A knowledge graph over GT Catalog data could enable multi-hop queries that vector retrieval struggles with: "What courses satisfy both the Intelligence and Info-Internetworks threads?" Implementation: use Microsoft's `graphrag` library or build a lightweight graph in PostgreSQL using the existing `chunks` and `documents` tables with extracted entities.

---

## 11. Prompt Engineering

No infrastructure changes needed — just better prompts.

### 11.1 Lost in the Middle

| | |
|---|---|
| **Paper** | *Lost in the Middle: How Language Models Use Long Contexts* — Liu et al., 2023 |
| **Link** | [arXiv:2307.03172](https://arxiv.org/abs/2307.03172) |
| **Complexity** | Trivial |
| **Priority** | Immediate |

**Core idea.** LLMs attend most strongly to the beginning and end of their context, with a U-shaped attention pattern. Documents in the middle of the context are most likely to be ignored.

**BuzzBot application.** When constructing the prompt in BuzzBot's answering step, place the most relevant chunks first and last, with less relevant ones in the middle. After reranking (§4), sort chunks so #1 is first, #2 is last, #3 is second, etc. This is a free improvement — just reorder the context in the prompt template in `prompts/`.

### 11.2 Chain-of-Thought Retrieval Prompting

| | |
|---|---|
| **Paper** | *Chain-of-Thought Prompting Elicits Reasoning in Large Language Models* — Wei et al., 2022 |
| **Link** | [arXiv:2201.11903](https://arxiv.org/abs/2201.11903) |
| **Complexity** | Trivial |
| **Priority** | Immediate |

**Core idea.** Instruct the LLM to reason step-by-step before answering: first identify which retrieved passages are relevant, then extract the specific information, then formulate the answer. This reduces hallucination by making the reasoning explicit.

**BuzzBot application.** Modify the answering prompt to require intermediate reasoning:
```
Given the retrieved passages, first identify which passages contain information
relevant to the query. Then extract the specific facts needed. Finally, compose
your answer using only those facts, citing each with [source_url].
```
BuzzBot's JSON output format already has a `notes` field that could capture this reasoning. Adjust the prompt template in `prompts/` to require step-by-step analysis before the answer.

### 11.3 Citation-Focused Prompting

| | |
|---|---|
| **Paper** | *Enabling Large Language Models to Generate Text with Citations* — Gao et al., 2023 |
| **Link** | [arXiv:2305.14627](https://arxiv.org/abs/2305.14627) |
| **Complexity** | Trivial |
| **Priority** | Immediate |

**Core idea.** Explicitly instruct the LLM to only make claims that can be supported by a specific citation from the context. If no passage supports a claim, the model should say "I don't have information about that" rather than generating unsupported text.

**BuzzBot application.** BuzzBot's grounding check already verifies citations post-hoc. Citation-focused prompting pushes this upstream — the LLM generates better citations in the first place, reducing the grounding check's rejection rate. Add explicit instructions:
```
Every factual claim MUST be supported by a direct quote from the provided passages.
If no passage supports a claim, do not make it. Instead, state that you don't have
enough information and suggest where the student might find the answer.
```

---

## Prioritized Recommendations

### Tier 1 — Immediate (1-2 weeks, high impact, low effort)

| # | Technique | Why First |
|---|---|---|
| 1 | **RAGAS evaluation** (§7.1) | Can't improve without measurement. Build the test set first. |
| 2 | **Cross-encoder reranking** (§4.1) | Highest retrieval quality gain per line of code. Use Cohere API or local `bge-reranker-v2-m3`. |
| 3 | **Lost-in-the-middle ordering** (§11.1) | Free — just reorder chunks in the prompt. |
| 4 | **Citation-focused prompting** (§11.3) | Free — modify prompt template to reduce hallucination. |
| 5 | **Chain-of-thought prompting** (§11.2) | Free — add reasoning step to reduce wrong answers from correct sources. |
| 6 | **HyDE** (§2.1) | Low cost, directly addresses query-document vocabulary mismatch. |
| 7 | **Multi-query retrieval** (§2.2) | Builds on existing RRF infrastructure. |

### Tier 2 — Near-Term (2-6 weeks, moderate effort)

| # | Technique | Why Next |
|---|---|---|
| 8 | **Parent-child retrieval** (§1.2) | Separates retrieval and generation granularity. |
| 9 | **Semantic chunking** (§1.4) | Better chunks = better retrieval at the source. |
| 10 | **CRAG** (§8.2) | Adds self-correction for retrieval failures; builds on reranker from Tier 1. |
| 11 | **Adaptive RAG routing** (§8.3) | Extends existing router with complexity-based strategies. |
| 12 | **Proposition-based chunking** (§1.1) | Best for dense factual pages (catalog, schedules). |
| 13 | **Embedding upgrade** (§6.1) | `text-embedding-3-large` at 1024-dim — simple config change + re-index. |
| 14 | **SPLADE** (§3.2) | Learned sparse retrieval replaces raw FTS. |
| 15 | **RAGChecker / TruLens** (§7.2, §7.3) | Deeper diagnostics once RAGAS baseline is established. |

### Tier 3 — Longer-Term (1-3 months, higher effort)

| # | Technique | Why Later |
|---|---|---|
| 16 | **RAFT fine-tuning** (§9.1) | Requires training data from production usage. |
| 17 | **Embedding fine-tuning** (§9.2) | Requires query-passage pairs from logs. |
| 18 | **GraphRAG** (§10.1) | High impact for multi-hop queries but significant engineering. |
| 19 | **RAPTOR** (§1.3) | Best for broad overview queries; complex tree construction. |
| 20 | **ColBERT** (§3.1) | Infrastructure replacement; consider only if retrieval is still insufficient after Tier 1-2. |

---

## References

1. Chen, T., et al. (2023). *Dense X Retrieval: What Retrieval Granularity Should We Use?* [arXiv:2312.06648](https://arxiv.org/abs/2312.06648)
2. Sarthi, P., et al. (2024). *RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval.* [arXiv:2401.18059](https://arxiv.org/abs/2401.18059)
3. Gao, L., et al. (2022). *Precise Zero-Shot Dense Retrieval without Relevance Labels (HyDE).* [arXiv:2212.10496](https://arxiv.org/abs/2212.10496)
4. Rackauckas, A. (2023). *RAG-Fusion: a New Take on Retrieval-Augmented Generation.* [arXiv:2402.03367](https://arxiv.org/abs/2402.03367)
5. Zheng, H., et al. (2023). *Take a Step Back: Evoking Reasoning via Abstraction in Large Language Models.* [arXiv:2310.06117](https://arxiv.org/abs/2310.06117)
6. Khattab, O., et al. (2022). *Demonstrate-Search-Predict: Composing retrieval and language model pipelines.* [arXiv:2212.14024](https://arxiv.org/abs/2212.14024)
7. Khattab, O., & Zaharia, M. (2020). *ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT.* [arXiv:2004.12832](https://arxiv.org/abs/2004.12832)
8. Formal, T., et al. (2021). *SPLADE v2: Sparse Lexical and Expansion Model for Information Retrieval.* [arXiv:2109.10086](https://arxiv.org/abs/2109.10086)
9. Reimers, N., & Gurevych, I. (2019). *Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks.* [arXiv:1908.10084](https://arxiv.org/abs/1908.10084)
10. Sun, W., et al. (2023). *Is ChatGPT Good at Search? Investigating Large Language Models as Re-Ranking Agents.* [arXiv:2304.09542](https://arxiv.org/abs/2304.09542)
11. Jiang, H., et al. (2023). *LongLLMLingua: Accelerating and Enhancing LLMs in Long Context Scenarios via Prompt Compression.* [arXiv:2310.06839](https://arxiv.org/abs/2310.06839)
12. Wang, Z., et al. (2023). *Learning to Filter Context for Retrieval-Augmented Generation (FILCO).* [arXiv:2311.08377](https://arxiv.org/abs/2311.08377)
13. Chen, J., et al. (2024). *BGE M3-Embedding: Multi-Lingual, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation.* [arXiv:2402.03216](https://arxiv.org/abs/2402.03216)
14. Wang, L., et al. (2024). *Improving Text Embeddings with Large Language Models.* [arXiv:2401.00368](https://arxiv.org/abs/2401.00368)
15. Es, S., et al. (2023). *RAGAS: Automated Evaluation of Retrieval Augmented Generation.* [arXiv:2309.15217](https://arxiv.org/abs/2309.15217)
16. Ru, D., et al. (2024). *RAGChecker: A Fine-grained Framework for Diagnosing Retrieval-Augmented Generation.* [arXiv:2408.08067](https://arxiv.org/abs/2408.08067)
17. Asai, A., et al. (2023). *Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection.* [arXiv:2310.11511](https://arxiv.org/abs/2310.11511)
18. Yan, S., et al. (2024). *Corrective Retrieval Augmented Generation.* [arXiv:2401.15884](https://arxiv.org/abs/2401.15884)
19. Jeong, S., et al. (2024). *Adaptive-RAG: Learning to Adapt Retrieval-Augmented Large Language Models through Question Complexity.* [arXiv:2403.14403](https://arxiv.org/abs/2403.14403)
20. Zhang, T., et al. (2024). *RAFT: Adapting Language Model to Domain Specific RAG.* [arXiv:2403.10131](https://arxiv.org/abs/2403.10131)
21. Edge, D., et al. (2024). *From Local to Global: A Graph RAG Approach to Query-Focused Summarization.* [arXiv:2404.16130](https://arxiv.org/abs/2404.16130)
22. Liu, N., et al. (2023). *Lost in the Middle: How Language Models Use Long Contexts.* [arXiv:2307.03172](https://arxiv.org/abs/2307.03172)
23. Wei, J., et al. (2022). *Chain-of-Thought Prompting Elicits Reasoning in Large Language Models.* [arXiv:2201.11903](https://arxiv.org/abs/2201.11903)
24. Gao, T., et al. (2023). *Enabling Large Language Models to Generate Text with Citations.* [arXiv:2305.14627](https://arxiv.org/abs/2305.14627)
