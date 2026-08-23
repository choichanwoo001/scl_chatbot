# 통합 평가 보고서

기준일: 2026-08-23

## 1. 결론

제출 범위의 로컬 회귀 테스트, 공개 데이터 무결성 검사, 검색 평가와 프런트 빌드는 통과했다.
현재 런타임은 OpenAI 실시간 채팅과 로컬 RDB를 사용하며 Vector Store와 실제 개인 결과 Gateway는
비활성 상태다.

## 2. 평가 환경

| 항목 | 환경 |
|---|---|
| 로컬 OS | Windows |
| 로컬 Python | 3.14 가상환경 |
| 백엔드 컨테이너 목표 | Python 3.13 slim |
| 프런트 빌드 | Node 22, Vite 6 |
| DB | SQLite `data/scl_catalog.db` |
| OCR | Tesseract `kor+eng` |
| Office 변환 | LibreOffice headless |
| 채팅 모델 | `gpt-5.6-luna` 설정 |

## 3. 현재 데이터 상태

2026-08-23 상태 API 기준이다.

| 데이터 | 건수 |
|---|---:|
| 검사항목 | 2,596 |
| 검사 변형 | 3,327 |
| 공개문서 | 2,058 |
| 추출 완료 첨부 | 1,862 |
| 용기 | 53 |
| 보존제 | 53 |
| 위치 | 66 |
| 사이트 경로 | 56 |
| 분류 용어 | 20 |
| 게시 FAQ | 0 |
| Vector 색인 대상 | 3,640 |
| Vector 색인 완료 | 0 |

마지막 검사 카탈로그 동기화는 `2026-08-20T02:34:13.311178`, 상태는 `completed`다.

## 4. 자동 검증 결과

| 검증 | 결과 | 판정 |
|---|---:|---|
| Ruff | 오류 0 | 통과 |
| 백엔드 pytest | 104/104 | 통과 |
| 프런트 단위 테스트 | 6/6 | 통과 |
| Sites worker·패키징 | 4/4 | 통과 |
| ESLint | 오류 0 | 통과 |
| 프런트 production build | 성공 | 통과 |
| 공개 검색 평가 | 17/17 | 통과 |
| Hit@K | 1.0 | 통과 |
| MRR | 1.0 | 통과 |
| 공개 데이터 무결성 | 실패·중복·orphan 0 | 통과 |

백엔드 테스트에는 Starlette TestClient의 `httpx2` 전환 관련 deprecation warning 1건이 있다.
현재 기능 실패는 아니지만 의존성 업그레이드 작업으로 추적한다.

## 5. 평가 방법

### 공개 검색

`data/evals/public_search_questions.json`의 17개 질문에 대해 기대 유형과 제목이 Top-K에 포함되는지
검사하고 reciprocal rank를 계산한다. 검사, 문서, 첨부, 보존제, 위치, 메뉴와 분류를 포함한다.

### 챗봇 시나리오

`data/evals/admin_chatbot_scenarios.json`은 검사 검색, 공문, 첨부, 오타, 후속 질문, 개인정보,
Prompt Injection, 개인 결과와 상담 전환을 포함한다. 2026-08-22 실제 OpenAI 관리자 평가에서는
19개 시나리오·20개 턴이 통과했으며 원본 결과는 아래 부록에 보관한다.

- `artifacts/admin-audit-2026-08-22/live-only-scenarios.json`
- `artifacts/admin-audit-2026-08-22/refactor-final-smoke.json`

이는 날짜가 있는 실행 증거이며, 모델·데이터 변경 후에는 다시 실행해야 한다.

### Vector 검색

`data/evals/vector_search_questions.json`에 14개 의미 질의를 준비했다. Provider·매핑·하이브리드
순위·장애 폴백은 mock 테스트로 검증했다. 실제 Store가 아직 없으므로 원격 Hit@K, MRR, 지연시간과
비용 결과는 제출 시점에 `미측정`이다.

## 6. 재현 명령

```powershell
$env:PYTHONPATH="backend"
.venv\Scripts\python -m ruff check --no-cache backend\app backend\tests scripts
.venv\Scripts\python -m pytest -p no:cacheprovider -q backend\tests
.venv\Scripts\python scripts\validate_public_data.py
.venv\Scripts\python scripts\evaluate_public_search.py
.venv\Scripts\python scripts\audit_external_integrations.py
.venv\Scripts\python scripts\audit_vector_store.py

Set-Location frontend
npm run lint
npm run test:unit
npm run test:sites
```

`test:sites`는 production build를 먼저 생성하고 Sites worker와 필수 산출물을 확인한다.

## 7. 화면 증거

- [데스크톱 시작 화면](../artifacts/admin-audit-2026-08-22/01-desktop-start.png)
- [HPV 검사 답변](../artifacts/admin-audit-2026-08-22/02-hpv-answer.png)
- [공문 답변](../artifacts/admin-audit-2026-08-22/03-document-answer.png)
- [개인 결과 인증 폼](../artifacts/admin-audit-2026-08-22/04-result-auth-form.png)
- [상담 접수 폼](../artifacts/admin-audit-2026-08-22/05-handoff-form.png)

## 8. 아직 측정하지 않은 항목

- 동시 사용자 수별 p50·p95·p99 응답시간
- 실제 OpenAI 토큰 비용과 사용자당 월 예상 비용
- 전체 Vector Store 저장·검색 비용
- 장시간 부하와 메모리 세션 증가량
- 실제 NVDA/VoiceOver와 200% 확대
- 실제 기관 Gateway의 인증·목록·상세 E2E

위 항목은 프로덕션 승인 기준에 포함하며 [한계와 로드맵](limitations-and-roadmap.md)에서 우선순위를
정의한다.
