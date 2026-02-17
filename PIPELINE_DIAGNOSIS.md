# BuzzBot Pipeline Diagnosis & 90%+ Accuracy Roadmap

## Executive Summary

BuzzBot의 vague/부정확한 답변은 단일 원인이 아니라 **파이프라인 전 단계에 걸친 복합적 문제**입니다. 아래에서 Ingestion → Storage → Retrieval → Generation 각 단계의 문제점과 해결방안을 정리합니다.

---

## 🔴 Stage 1: Ingestion (Data Collection & Preprocessing)

### 문제 1.1 — Chunk에 구조적 메타데이터가 부족
**현재**: `chunk_text()` 함수가 순수 텍스트만 500토큰 단위로 자름. 페이지 내 headings, 섹션 계층, 날짜 등의 구조가 chunk 분할 시 손실됨.

**증거**: `ingestion/chunk.py` — 토큰 position 기반 sliding window만 사용. heading boundary를 인식하지 않음.

**영향**: "Spring 2026 Registration Deadline"이라는 정보가 "Registration" heading 아래 있어도 chunk가 heading 경계를 무시하고 잘리면 context가 깨짐.

**해결**:
- Heading-aware chunking 도입 — `##` / `<h2>` 등의 heading boundary를 chunk 분할 기준으로 우선 사용
- 각 chunk의 metadata에 `section_heading`, `page_section_path` 추가
- 테이블 데이터를 별도 chunk type으로 처리 (학사 캘린더 = 테이블 기반 데이터)

### 문제 1.2 — Extract에서 테이블 데이터 손실
**현재**: `trafilatura.extract(include_tables=True)` 사용하지만, 학사 캘린더 같은 HTML table이 plain text로 변환되면서 행/열 관계가 파괴됨.

**증거**: `ingestion/extract.py` — trafilatura output은 테이블을 단순 텍스트로 flatten함.

**영향**: 
```
원본: | Event | Date |
      | Registration Deadline | Aug 15, 2026 |
      
추출 결과: "Registration Deadline Aug 15 2026 Add/Drop Deadline Sep 1..."
→ 어떤 날짜가 어떤 이벤트인지 연결 관계 손실
```

**해결**:
- 테이블을 감지하고 Markdown table 또는 "Event: X, Date: Y" 같은 구조화된 format으로 변환
- 또는 테이블 행을 개별 chunk로 생성 (key-value pair 보존)

### 문제 1.3 — GT Scheduler 데이터의 chunk가 너무 세분화됨
**현재**: `gt_scheduler.py`에서 각 section마다 별도 chunk 생성 → CS 4400이 10개 section이면 10개 chunk.

**영향**: 검색 시 "CS 4400 is offered in Spring 2025" 같은 overview chunk가 없고, section별 세부정보만 있어서 "is it offered?" 같은 질문에 직접 대답할 context가 부족.

**해결**:
- 과목별 **summary chunk** 추가: "CS 4400 - Introduction to Database Systems is offered in Spring 2025 with N sections. Instructors: [list]. CRNs: [list]."
- Summary chunk를 section chunk보다 높은 우선순위로 retrieval

---

## 🟡 Stage 2: Storage & Metadata

### 문제 2.1 — metadata_json 활용 부족
**현재**: Chunk 모델에 `metadata_json` 필드가 있고, gt-scheduler chunk에는 `course_code`, `term_name` 등이 저장됨. 하지만 일반 웹 크롤링 chunk에는 metadata가 `{url, title, source, fetched_at}` 정도뿐.

**해결**:
- 모든 chunk에 `content_type` (calendar_event, course_description, policy, FAQ, table_row 등) 태깅
- `date_mentioned` 필드 — chunk 내 날짜 reference 자동 추출
- `term_relevance` — 어떤 학기에 해당하는 정보인지 태깅

### 문제 2.2 — Source 필터링과 retrieval의 mismatch
**현재**: Router가 `source_filter="gt-registrar"`로 필터하면 gt-catalog 결과는 완전 제외됨. 하지만 일부 질문은 cross-source 정보가 필요.

**영향**: "When is the registration deadline for CS 4400 Spring 2025?" → registrar calendar + course schedule 모두 필요하지만 source_filter가 하나만 선택.

**해결**:
- Router에서 `source_filter`를 list로 변경 (복수 source 허용)
- 또는 primary + secondary source 개념 도입

---

## 🔴 Stage 3: Retrieval (가장 영향 큰 단계)

### 문제 3.1 — Query Rewrite가 구현되어 있지만 연결 안 됨 ⚠️ CRITICAL
**현재**: `prompts/20_query_rewrite_retrieval.md`에 query rewrite 프롬프트가 정의되어 있지만 **chat.py 파이프라인에서 호출하지 않음**. 사용자 쿼리가 그대로 embedding됨.

**증거**: `app/api/chat.py` line 108 — `get_query_embedding(query)` 호출 시 raw query 사용.

**영향**: 
- "When is the registration deadline?" → 이 질문 그대로 embedding → 너무 일반적 → vague한 chunk 반환
- Query rewrite가 동작했다면: "Georgia Tech Spring 2026 registration deadline date" → 훨씬 specific한 검색

**해결**:
- `_rewrite_query()` 함수 구현 — LLM으로 query를 rewrite한 후 retrieval에 사용
- 현재 날짜/학기 정보 자동 주입
- Cost 우려 시: LLM rewrite 대신 rule-based enrichment (현재 학기 + 날짜 append)

### 문제 3.2 — 현재 학기/날짜 context 없음 ⚠️ CRITICAL
**현재**: "When is the registration deadline?"에서 어떤 학기인지 추론 불가. 시스템이 현재 날짜를 query context에 주입하지 않음.

**해결**:
- Router 또는 retrieval 단계에서 현재 날짜 기준 학기 자동 추론
- `date_sensitive` 쿼리에 자동으로 현재 학기 term 추가
- user_template.txt에 `{{CURRENT_DATE}}`, `{{CURRENT_TERM}}` 변수 추가

### 문제 3.3 — Vector search만으로 부족할 때의 fallback 전략
**현재**: Hybrid retrieve (vector + FTS + exact_code)가 있지만, `rag_skip_fts_when_vector_sufficient=True` 설정으로 vector 결과가 top_k개 이상이면 FTS를 skip.

**영향**: Vector search가 semantically similar하지만 정확하지 않은 chunk를 충분히 반환하면, 정확한 키워드 매칭 결과(FTS)가 제외됨.

**해결**:
- `rag_skip_fts_when_vector_sufficient` 기본값을 `False`로 변경하거나, 최소한 date-sensitive 쿼리에서는 항상 FTS 병행
- RRF fusion weight를 FTS 쪽으로 조정 (정확한 키워드 매칭에 더 높은 가중치)

### 문제 3.4 — Live Fetch 결과의 relevance scoring이 primitive
**현재**: `live_fetch.py`에서 `_overlap_score()`는 단순 token overlap (query와 chunk의 공통 token 비율). 

**영향**: "When is the registration deadline?" 질문에 live fetch로 registrar 전체 페이지를 가져오면 수십 개 chunk 중 상위 8개가 반드시 정답 chunk가 아님.

**해결**:
- Live fetch chunk도 embedding-based reranking 적용
- 또는 cross-encoder reranker 도입 (e.g., BAAI/bge-reranker)

---

## 🟡 Stage 4: Answer Generation

### 문제 4.1 — Context window가 vague한 chunk로 채워짐
**현재**: `_build_context()`가 상위 chunk들을 3000 토큰까지 채움. 하지만 retrieval이 vague한 chunk를 반환하면 context 자체가 vague.

**영향**: LLM이 아무리 좋아도 context에 정확한 정보가 없으면 정확한 답변 불가 → "it varies by semester" 같은 hedge 답변.

**해결**: 이건 retrieval 품질이 해결되면 자동으로 개선됨.

### 문제 4.2 — System prompt가 yes/no 질문 처리를 유도하지만 specificity는 부족
**현재**: `chat_system.txt`에 "If query is yes/no and contexts explicitly support it, start with direct yes/no" 규칙 있음. 하지만 "When" 질문에 대해 구체적 날짜를 강제하는 규칙이 없음.

**해결**: 
```
RULES 추가:
- If the query asks "when", provide the EXACT date from contexts. 
  Never answer with "it varies" if a specific date exists in contexts.
- If contexts contain data for a specific term, mention that term explicitly.
- If the user didn't specify a term, answer for the CURRENT or NEXT upcoming term 
  based on {{CURRENT_DATE}}.
```

### 문제 4.3 — Multi-turn conversation history 없음
**현재**: 각 메시지가 독립적. "it"이 뭘 가리키는지 모름.

**영향**: Follow-up 질문 ("is it offered in Spring 2025?")에서 "it" = CS 4400 해석 불가.

**해결**: 
- ChatRequest에 `history` 필드 추가
- Frontend에서 최근 N개 (3-5개) turn 전송
- 답변 생성 전 LLM으로 standalone query rewrite ("it" → "CS 4400")

### 문제 4.4 — Model 선택
**현재**: `gpt-4o-mini` — 비용 효율적이지만, complex reasoning/extraction에서 `gpt-4o`보다 약함.

**영향**: 동일한 context가 주어져도 mini가 핵심 정보를 놓치거나 hedge하는 빈도가 높음. 특히 context가 noisy할 때.

**해결**:
- Retrieval 품질이 좋으면 mini도 충분. Retrieval 먼저 개선 후 model 평가.
- 대안: router intent에 따라 모델 분기 (간단한 catalog 질문 → mini, deadline/calendar 질문 → gpt-4o)

---

## 📋 Priority-Ordered Fix Roadmap

### Phase 1: Highest Impact, Low-Medium Effort (1-2일)
| # | Fix | 파일 | 예상 효과 |
|---|-----|------|-----------|
| 1 | **현재 학기/날짜 자동 주입** | `chat.py`, `chat_user_template.txt` | Date-sensitive 질문 정확도 40→80% |
| 2 | **Query rewrite 파이프라인 연결** | `chat.py`, 새 `app/rag/query_rewrite.py` | 모든 질문의 retrieval precision 향상 |
| 3 | **FTS skip 비활성화** | `config.py` (설정값 변경) | Keyword-exact 결과 보존 |
| 4 | **System prompt specificity 강화** | `chat_system.txt` | Hedge 답변 감소 |

### Phase 2: Medium Impact, Medium Effort (3-5일)
| # | Fix | 파일 | 예상 효과 |
|---|-----|------|-----------|
| 5 | **Heading-aware chunking** | `ingestion/chunk.py` | 구조적 정보 보존 (재ingestion 필요) |
| 6 | **테이블 데이터 구조 보존** | `ingestion/extract.py` | Calendar/schedule 정확도 대폭 향상 |
| 7 | **GT Scheduler summary chunk** | `ingestion/gt_scheduler.py` | "Is X offered?" 질문 정확도 |
| 8 | **Multi-turn history** | `schemas/chat.py`, `chat.py`, frontend | Follow-up 질문 해결 |

### Phase 3: Polish & Reliability (1주)
| # | Fix | 파일 | 예상 효과 |
|---|-----|------|-----------|
| 9 | **Cross-encoder reranker** | `app/rag/retrieval.py` | Retrieval precision +10-15% |
| 10 | **Chunk metadata enrichment** | `ingestion/run_ingestion.py` | 필터링 정확도 |
| 11 | **Eval harness 활용** | `eval/rag_eval.py` | 회귀 방지, 정량적 추적 |
| 12 | **Adaptive model routing** | `app/rag/answerer.py` | 복잡한 질문에 강한 모델 사용 |

---

## 🎯 90%+ 정확도 달성 기준

현재 eval 프레임워크 (`eval/metrics.md`)에서 정의한 목표:
- **Coverage@5 ≥ 0.85** — 상위 5개 chunk에 정답 포함
- **Grounding pass rate ≥ 0.90** — 답변이 source에 근거
- **Freshness correctness ≥ 0.95** — deadline 질문에 최신 데이터 사용

**현재 추정치 vs 목표:**
| Metric | 현재 (추정) | 목표 | Gap 원인 |
|--------|-------------|------|----------|
| Coverage@5 | ~0.55 | 0.85 | Query rewrite 미사용, FTS skip |
| Grounding | ~0.75 | 0.90 | Vague context → vague answer |
| Freshness | ~0.40 | 0.95 | 현재 학기 미주입, live fetch scoring |

**Phase 1만 완료해도 Coverage@5 → ~0.75, Freshness → ~0.80 도달 예상.**
**Phase 1+2 완료 시 전체 지표 0.85+ 도달 가능.**
