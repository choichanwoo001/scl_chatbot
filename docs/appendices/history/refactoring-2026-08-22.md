# SCL 챗봇 리팩토링 결과

## 범위

기능, HTTP 계약, 실시간 OpenAI 전용 정책, 화면 디자인을 유지하면서 공통 코드 재사용과 책임 분리를 진행했다.

## 주요 변경

- 브라우저 prepared fallback 엔진과 중복 guardrail 제거
- 서버 세션과 중복되던 프런트 `lastTest`, `context`, `nextContext` 제거
- 채팅 UI를 reducer, controller hook, 폼, 메시지 renderer로 분리
- FastAPI를 앱 팩토리, 서비스 컨테이너, 공통 예외 처리, 기능별 router로 분리
- 오케스트레이터에서 정책 보정, RDB grounding, 오프라인 개발 응답 분리
- OpenAI 예외 처리 범위를 gateway 호출로 제한해 내부 코드 오류가 fallback으로 숨지 않게 변경
- taxonomy 직접 SQL을 repository로 이동
- 공개 데이터 수집 이력을 공통 repository로 이동
- 첨부 포맷 분기를 extractor registry로 변경
- Ruff와 ESLint 정적 검사 추가

## 현재 구조

```text
frontend/src/chat/
  ChatWidget.jsx
  ChatForms.jsx
  MessageBody.jsx
  chatState.js
  useChatController.js

backend/app/
  api/
    chat.py
    catalog.py
    public_data.py
    results.py
    workflows.py
  repositories/
    taxonomy.py
    sync_runs.py
  chat_policy.py
  chat_grounding.py
  offline_chat.py
  dependencies.py
  exception_handlers.py
  main.py
```

## 검증 결과

- Ruff: 통과
- ESLint: 통과
- backend pytest: 91 passed
- frontend unit: 10 passed
- Sites worker: 4 passed
- production build: 성공
- 공개 데이터 무결성: 성공
- 공개 검색: 17/17, Hit@K 1.0, MRR 1.0
- 리팩토링 후 전체 실시간 관리자 시나리오: 19/19, 20/20턴, 전부 `mode=openai`
- 최종 gateway 예외 범위 축소 후 smoke: 3/3 시나리오, 4/4턴 통과

전체 실시간 결과는 `artifacts/admin-audit-2026-08-22/refactor-live-scenarios.json`, 최종 smoke는 `artifacts/admin-audit-2026-08-22/refactor-final-smoke.json`에 저장했다.

## 의도적으로 유지한 부분

- SQLAlchemy `models.py`는 관계 선언의 순환 import 위험이 커서 이번에는 물리적으로 분할하지 않았다.
- `scl_public_data.py`의 데이터셋별 파서는 소스 HTML 구조가 서로 달라 억지로 하나의 generic parser로 합치지 않았다. 공통 실행 이력과 저장 경계만 먼저 추출했다.
- 개인 결과 provider의 Strategy 구조는 이미 적절하므로 유지했다.
- 오프라인 응답은 단위 테스트와 명시적 로컬 개발 요청을 위해 별도 서비스로 남겼다. 공개 프런트 요청에는 사용되지 않는다.
