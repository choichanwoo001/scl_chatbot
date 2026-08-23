# SCL 챗봇 OpenAI API 전환 구현 계획

작성일: 2026-08-15  
전제: **SCL 공식 검사 DB 연동만 제외하고 실제 동작 가능한 범위를 구현한다.** 현재 홈페이지형 UI는 유지하고, 정규식·고정 답변 기반 데모 엔진을 OpenAI API 기반 자연어 처리로 교체한다.

## 실행 현황 — 2026-08-18

완료:

- FastAPI 백엔드, `/health`, `/api/chat`, CORS, 공통 응답 스키마
- 서버 전용 OpenAI SDK와 Responses API Structured Outputs 호출 경로
- `omni-moderation-latest` 입력 검사 경로
- 개인정보 마스킹, 욕설 분기, 반복 입력, 프롬프트 공격 방어
- DB 대신 `DEMO-` 샘플 JSON 검사 검색
- 인메모리 세션과 후속 질문 문맥
- React 챗봇의 백엔드 API 연결, loading·오류·fallback 상태
- OpenAI File Search용 공개문서 샘플과 적재 스크립트
- Docker Compose와 GitLab 테스트·빌드 파이프라인
- 백엔드 9개, 프런트엔드 9개 자동 테스트 및 프로덕션 빌드 통과

외부 설정이 있어야 완료할 수 있는 항목:

- 실제 OpenAI 응답 검증: `OPENAI_API_KEY` 필요
- 공개문서 RAG 실제 적재: API 키로 스크립트 실행 후 `OPENAI_VECTOR_STORE_ID` 설정 필요
- 실제 SCL 검사정보 정확성: 공식 검사 DB 또는 승인 데이터 필요
- 실제 상담 티켓 전송: 상담 시스템 API 또는 수신 채널 필요

## 1. 이번 전환의 목표

현재 `frontend/src/lib/demoEngine.js`가 담당하는 키워드 분기와 고정 답변을 서버 기반 대화 오케스트레이터로 대체한다.

구현 목표는 다음과 같다.

- 자유로운 한국어 질문, 오타, 영문명, 약어, 유사 표현 이해
- 검사명·검체·용기·소요일·검사 가능일 등 여러 조건을 한 문장에서 추출
- 모호한 질문에는 임의로 답하지 않고 추가 질문
- 같은 세션에서 앞서 말한 검사와 조건을 기억
- SCL 공개 FAQ·공지·안내 문서를 근거로 답변하고 출처 표시
- 욕설, 개인정보, 프롬프트 공격, 위험하거나 부적절한 입력에 대한 분기 처리
- 답을 찾지 못하거나 실제 확인이 필요한 경우 상담 연결·문의 접수 흐름 제공
- 스트리밍 답변, 오류 복구, 피드백, 미해결 질문 수집
- GitLab에서 프런트엔드·백엔드·테스트·CI 구성을 함께 확인 가능

## 2. 이번 범위에서 가능한 것과 불가능한 것

### 구현 가능

- OpenAI API를 이용한 의도 분류와 항목 추출
- 복합 질문 분해 및 후속 질문 생성
- 대화 문맥 유지
- OpenAI File Search를 이용한 공개 문서 RAG
- 샘플 JSON 검사 목록 검색과 비교
- 답변 근거와 출처 링크 표시
- 입력·출력 안전 필터
- 상담 연결 UI와 문의 접수 데모
- 세션 요약, 피드백, 미해결 질문 기록
- 관리자용 데모 지표
- Docker와 GitLab CI/CD

### 공식 DB 없이는 보장할 수 없음

- 전체 검사 항목을 빠짐없이 검색하는 것
- 실제 운영 중인 최신 검사코드, 검체·용기, 검사일, 소요일의 정확성
- 개인 검사결과 조회, 로그인, EMR 연동
- 실제 상담 시스템에 티켓을 생성하는 것

따라서 검사 상세 답변은 `샘플 데이터` 배지를 붙이고, 공개 문서 RAG 답변은 출처와 기준일을 표시한다. 근거가 없으면 모델이 추측하지 않고 확인 불가 또는 상담 전환으로 끝내야 한다.

## 3. 목표 구조

```text
사용자
  -> 기존 React 챗봇 UI
  -> POST /api/chat (SSE 스트리밍)
  -> 길이·빈도·개인정보 로컬 검사
  -> OpenAI Moderation
  -> OpenAI Responses API
       1) Structured Outputs로 의도·조건 추출
       2) 필요하면 추가 질문
       3) 공개 문서는 File Search
       4) 검사 항목은 샘플 JSON 검색 도구 호출
       5) 상담·문의는 별도 도구 호출
  -> 출처·의료 표현·개인정보 출력 검증
  -> 답변 스트리밍
  -> 세션 요약·피드백·미해결 질문 기록
```

OpenAI API 키는 프런트엔드에 넣지 않고 백엔드 환경변수로만 관리한다. 모델명도 `OPENAI_CHAT_MODEL` 환경변수로 분리해 계정 가용성과 평가 결과에 따라 교체할 수 있게 한다.

## 4. 자연어 처리 계약

모델의 첫 결과는 자유 텍스트가 아니라 다음 형태의 구조화 데이터로 받는다.

```json
{
  "domain": "test | result | document | corporate_content | account | support | unsupported",
  "sub_intent": "get_test_detail",
  "requested_action": "search | explain | summarize | compare | navigate | download | authenticate | contact",
  "matched_test_code": null,
  "matched_test_variant_key": null,
  "candidate_test_codes": [],
  "candidate_test_variant_keys": [],
  "matched_document_ids": [],
  "matched_content_id": null,
  "target_route": null,
  "target_department": null,
  "requested_fields": [],
  "needs_clarification": false,
  "clarification_question": null,
  "choices": [],
  "requires_authentication": false,
  "needs_handoff": false,
  "medical_review_required": false,
  "has_multiple_intents": false,
  "confidence": 0.0
}
```

`domain`은 사용자가 문의한 업무 영역, `sub_intent`는 영역 안의 구체적인 목적, `requested_action`은 검색·설명·이동 등 기대 행동을 나타낸다. 페이지 이동과 상담 연결은 업무 영역과 분리하여 각각 `requested_action=navigate`, `needs_handoff=true`로 표현한다. 이 결과를 기준으로 서버 코드가 검사 검색, 결과조회 안내, 문서 RAG, 페이지 이동, 인증 안내, 상담 전환 중 하나를 선택한다. 모델이 검사정보나 식별자, 경로를 기억으로 지어내는 경로는 허용하지 않는다.

## 5. 입력 방어 브랜치

입력 방어는 한 번의 욕설 필터가 아니라 다음 순서로 처리한다.

1. 서버에서 입력 길이, 요청 빈도, 제어문자, 비정상 반복 검사
2. 주민등록번호·전화번호·이메일·개인 검사결과 표현 탐지 및 마스킹
3. `omni-moderation-latest`로 유해성 신호 확인
4. 프롬프트 인젝션과 시스템 정보 탈취 시도 탐지
5. 정책에 따라 아래처럼 분기

| 입력 | 처리 |
|---|---|
| 정상 문의 | 그대로 처리 |
| 욕설 + 정상 검사 문의 | 표현 안내 후 문의는 계속 처리 |
| 욕설·도배만 있음 | 1회 안내, 반복 시 잠시 제한 |
| 개인정보 포함 | 원문을 저장하지 않고 마스킹 후 안전한 채널 안내 |
| 프롬프트 공격 | 공격 지시는 무시하고 SCL 문의 범위로 복귀 |
| 자해·위협·응급 표현 | 일반 검사 안내를 중단하고 적절한 긴급 안내 |
| 진단·치료·개인 결과 해석 | 의료 판단을 하지 않고 의료진·인증 채널로 전환 |

출력에도 동일하게 출처 누락, 개인 결과 해석, 진단·치료 단정, 내부 프롬프트 노출 여부를 검사한다.

## 6. 작은 작업 단위별 구현 순서

### 0단계 — 범위 고정과 평가 질문 준비

- [ ] 공식 검사 DB 제외 범위를 README와 화면에 표시
- [ ] 대표 질문 60개, 복합·후속 질문 20개, 안전 질문 30개 작성
- [ ] 각 질문의 기대 의도, 필수 항목, 허용 답변을 JSON으로 정의
- [ ] 모델명, 최대 출력 길이, 시간 제한, 비용 상한을 환경변수로 정의

완료 기준: 구현 전후를 같은 질문 세트로 비교할 수 있다.

### 1단계 — 백엔드 뼈대

- [ ] `backend/`에 FastAPI 프로젝트 생성
- [ ] `GET /health`, `POST /api/chat` 구현
- [ ] 요청·응답 Pydantic 스키마 작성
- [ ] CORS, 타임아웃, 재시도, 공통 오류 응답 구현
- [ ] `.env.example`에 OpenAI 관련 변수 추가
- [ ] API 키가 로그와 브라우저 번들에 포함되지 않는 테스트 추가

완료 기준: OpenAI 호출 없이도 프런트엔드에서 서버 health와 mock chat 응답을 받을 수 있다.

### 2단계 — OpenAI Responses API 연결

- [ ] 공식 Python SDK 추가
- [ ] 서버 전용 OpenAI 클라이언트 작성
- [ ] Responses API의 스트리밍 응답을 SSE로 전달
- [ ] API 오류, 제한 초과, 타임아웃 시 사용자용 복구 문구 구현
- [ ] 모델명을 코드에 고정하지 않고 환경변수로 선택

완료 기준: 자유 질문에 실제 모델 응답이 스트리밍되고 키가 브라우저에 노출되지 않는다.

### 3단계 — 의도·조건 추출과 대화 기억

- [ ] Structured Outputs 스키마 구현
- [ ] 의도, 검사명, 검체, 용기, 소요일, 일정, 긴급성 추출
- [ ] 여러 요구사항을 한 질문에서 분리
- [ ] 후보가 여러 개거나 조건이 부족하면 추가 질문
- [ ] `previous_response_id` 또는 서버가 관리하는 최근 대화 요약으로 문맥 유지
- [ ] 세션 만료와 새 대화 시작 처리

완료 기준: `HPV 검사 알려줘 -> 그거 무슨 용기 써?`와 같은 후속 질문이 안정적으로 이어진다.

### 4단계 — DB 없는 검색과 RAG

- [ ] `data/fixtures/tests.json`에 발표용 검사 샘플 작성
- [ ] `search_demo_tests` 도구 함수 구현
- [ ] 검사명, 코드, 약어, 영문명, 검체, 소요일 조건 필터 구현
- [ ] SCL 공개 FAQ·공지·안내 문서를 정리하고 출처 URL·기준일 포함
- [ ] 문서를 OpenAI Vector Store에 업로드하는 스크립트 작성
- [ ] Responses API File Search 연결
- [ ] 답변에 출처 제목, URL, 기준일을 매핑
- [ ] 근거가 없는 답변은 생성하지 않고 상담 전환

완료 기준: 샘플 검사 검색과 공개 문서 질문은 출처가 있는 답변을 하고, 없는 정보는 추측하지 않는다.

### 5단계 — 입력·출력 방어

- [ ] 기존 클라이언트 정규식을 서버 모듈로 이전하고 한글 인코딩 문제 수정
- [ ] 개인정보 마스킹 테스트 추가
- [ ] OpenAI Moderation 연결
- [ ] 욕설 단독, 욕설 포함 정상 문의, 도배, 프롬프트 공격 분기 구현
- [ ] 의료 고위험 질문과 개인 결과 해석 금지 정책 구현
- [ ] 출력 인용·금칙 표현 검증기 구현
- [ ] IP·세션 단위 rate limit 구현

완료 기준: 안전 테스트 30개와 프롬프트 공격 테스트가 모두 기대 분기로 처리된다.

### 6단계 — 상담 전환과 운영 기록

- [ ] 상담 가능 시간과 연락처를 설정 파일로 분리
- [ ] 답변 불가 시 대화 요약을 자동 생성
- [ ] `POST /api/handoff`, `POST /api/feedback` 구현
- [ ] 실제 상담 연동 전에는 JSON 파일 또는 SQLite로 데모 기록
- [ ] 미해결 질문, 전환 사유, 안전 이벤트를 개인정보 제거 후 기록
- [ ] 관리자용 요약 화면에 문의 수, 미해결 질문, 전환 사유 표시

완료 기준: 상담 연결 버튼을 누르면 질문 요약과 출처·처리 상태를 운영 화면에서 확인할 수 있다.

### 7단계 — 기존 화면에 실제 API 연결

- [ ] `buildDemoReply()` 직접 호출 제거
- [ ] `frontend/src/lib/chatApi.js` 추가
- [ ] 스트리밍 중 상태, 취소, 재시도, 네트워크 오류 UI 구현
- [ ] 출처 drawer와 샘플 데이터 배지 구현
- [ ] 추가 질문 선택지, 피드백, 상담 접수 UI 연결
- [ ] 데스크톱 고정형 채팅 패널에서 전체 기능 회귀 검증
- [ ] API 미설정 시에만 명시적인 데모 모드 fallback 제공

완료 기준: 기존 SCL 홈페이지 화면 위 챗봇이 OpenAI 백엔드로 실제 동작한다.

### 8단계 — 평가와 GitLab 시연 패키지

- [ ] 백엔드 단위·통합 테스트
- [ ] 프런트엔드 컴포넌트·E2E 테스트
- [ ] 대표 질문 자동 평가와 회귀 테스트
- [ ] Docker Compose 구성
- [ ] `.gitlab-ci.yml`에 test -> build -> package 단계 구성
- [ ] GitLab CI Variables에 API 키를 넣는 방법 문서화
- [ ] 3~5분 발표 시나리오와 시연 실패 시 녹화본 준비
- [ ] Figma 파이프라인의 구현 상태 색상 업데이트

완료 기준: 새 환경에서 문서대로 실행할 수 있고, CI가 테스트·빌드를 통과한다.

## 7. 예상 파일 구조

```text
scl_chat_project/
├─ frontend/
│  └─ src/lib/chatApi.js
├─ backend/
│  ├─ app/main.py
│  ├─ app/api/chat.py
│  ├─ app/core/config.py
│  ├─ app/openai/client.py
│  ├─ app/chat/orchestrator.py
│  ├─ app/chat/schemas.py
│  ├─ app/guardrails/input.py
│  ├─ app/guardrails/output.py
│  ├─ app/tools/test_search.py
│  ├─ app/tools/handoff.py
│  └─ tests/
├─ data/
│  ├─ fixtures/tests.json
│  └─ scl-documents/
├─ evals/
│  ├─ questions.json
│  └─ safety.json
├─ scripts/ingest_openai_files.py
├─ docker-compose.yml
├─ .env.example
└─ .gitlab-ci.yml
```

## 8. 품질 기준

- 의도·필드 JSON 스키마 유효율 100%
- 평가 세트 의도 분류 정확도 90% 이상
- 근거가 필요한 답변의 출처 포함률 100%
- 근거 없는 검사코드·소요일 생성 0건
- 개인정보 원문 로그 저장 0건
- 프롬프트 공격으로 시스템 지침·비밀 노출 0건
- 후속 질문 시 대상 검사 유지 성공률 90% 이상
- 첫 응답 스트리밍 시작 목표 2초 이내, 일반 답변 완료 목표 5초 이내

속도 목표는 실제 배포 지역, 모델, 네트워크에서 측정한 뒤 조정한다.

## 9. 예상 일정

1명이 구현할 때 약 8~12 작업일을 예상한다.

| 구간 | 예상 |
|---|---:|
| 백엔드와 OpenAI 연결 | 2일 |
| 의도 추출과 세션 문맥 | 1.5일 |
| 샘플 검색과 공개 문서 RAG | 2일 |
| 입력·출력 방어 | 1.5일 |
| 상담·피드백·운영 화면 | 1일 |
| 프런트 연결과 UX | 1일 |
| 평가·Docker·GitLab CI | 1~3일 |

## 10. 구현 시작 순서

첫 번째 구현 묶음은 `1단계 백엔드 뼈대 -> 2단계 OpenAI 연결 -> 7단계 프런트 최소 연결` 순서로 진행한다. 이 묶음이 끝나면 홈페이지 위 챗봇에서 실제 자유 입력 응답을 먼저 확인할 수 있다. 이후 `3단계 구조화 이해 -> 4단계 RAG -> 5단계 방어`를 추가해 정확성과 안전성을 높인다.

이 순서라면 각 단계가 끝날 때마다 브라우저에서 확인 가능한 결과가 생기며, 마지막에 한꺼번에 연결하다가 실패하는 위험을 줄일 수 있다.
