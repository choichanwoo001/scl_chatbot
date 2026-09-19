# SCL 챗봇 관리자 시연 준비 결과

- 점검일: 2026-08-22
- 판정: **공개 정보 실시간 관리자 시연 준비 완료**
- 실제 사용 모드: OpenAI `gpt-5.6-luna` + 로컬 SCL RDB, Vector Store 미사용
- 실시간 계약: 모든 관리자 시나리오 `require_live=true`; OpenAI/RDB 오류 시 prepared fallback 없이 HTTP 503
- 개인 결과조회: mock 비활성화. 채팅 내 인증 폼까지 표시되며 실제 조회는 기관 Gateway 정보 대기

## FAQ와 추가 시나리오 범위

현재 DB의 `published` FAQ는 0건이다. 아직 사용자 피드백이 수집되지 않았기 때문에 정상 상태다. 이번 점검에서는 다음 세 종류를 FAQ 범위로 정의했다.

1. 화면 빠른 질문 3건
   - 갑상선 관련 검사
   - HPV 용기와 소요일
   - 2026년 8월 연휴 검사일정 공문
2. 이전 빠른 질문 회귀 1건
   - 혈액 검사 중 결과 빠른 것
3. `public-faq.md`의 운영 계약 6건
   - 검사 카탈로그 근거 사용
   - 개인정보 가림
   - 개인 결과 인증
   - 개인 결과 의료 판단 제한
   - 상담 연결
   - 근거 없는 검사정보 추측 금지

여기에 prompt injection, 지원 범위 밖 질문, 지점 연락처, 홈페이지 메뉴, 일반 검사의뢰서, OCR 첨부 검색, 오타, 2턴 후속 질문, 계정 복구를 추가해 총 19개 시나리오·20개 대화를 구성했다.

시나리오 원본은 `data/evals/admin_chatbot_scenarios.json`, 실행기는 `scripts/evaluate_admin_chatbot.py`, 실시간 전용 원본 결과는 `artifacts/admin-audit-2026-08-22/live-only-scenarios.json`에 있다.

## 반복 실행과 수정 결과

| 반복 | 결과 | 발견 내용 | 조치 |
|---|---:|---|---|
| 기준선 | 공개 검색 16/17 | 일반 검사의뢰서가 전문 의뢰서들 뒤로 밀림 | 일반 SCL 의뢰서 우선 tie-breaker와 다운로드 의도 boost 추가 |
| 관리자 시나리오 1차 | 13/19 | 지점·메뉴 하위 의도 누락, 문서 요청이 검사로 분류, 후보 근거 누락 | support 하위 의도·문서 다운로드 의도·후보 citation 추가 |
| 2차 | 14/19 | Structured Output 조합 오류가 전체 fallback 유발, 개인 결과 flags 변동 | 검증 실패 1회 재시도, 인증·의료 handoff 정책을 서버에서 강제 |
| 3차 | 18/19 | OCR 검색의 일반 의도어가 핵심어를 희석, 후속 질문 ref 변동 | 문서 의도어 stop word 처리, RDB 문서 보강, 세션 variant 우선 |
| 최종 | **19/19** | 없음 | 2턴 후속 질문도 별도 3회 연속 통과 |

관리자 화면의 세 번째 빠른 질문은 너무 넓어 전문 FISH 검사를 대표 답처럼 보일 수 있던 `혈액 검사 중 결과 빠른 것` 대신, 문서 검색을 명확히 보여주는 `2026년 8월 연휴 검사일정 공문`으로 교체했다. 이전 질문은 회귀 평가에서 계속 검사한다.

## 최종 자동 검증

| 구분 | 결과 |
|---|---:|
| 관리자 실제 OpenAI API + RDB 시나리오 | **19/19 시나리오, 20/20 턴 통과, 전 턴 `mode=openai`** |
| 기존 OpenAI Structured Output 평가 | **9/9 통과** |
| 공개 RDB 검색 평가 | **17/17, Hit@K 1.0, MRR 1.0** |
| backend fallback 차단 회귀 테스트 | **키 없음 + `require_live=true` → HTTP 503, prepared 응답 없음** |
| 백엔드 pytest | **91 passed** |
| 프런트 단위 테스트 | **12 passed** |
| Sites worker 테스트 | **4 passed** |
| 프런트 production build | **성공** |
| 공개 데이터 무결성 | **성공, FK/중복/orphan/실패 추출 0** |
| Docker 이미지 | `scl-chatbot:admin-ready` 빌드 성공 |
| Docker 런타임 | health 200, 문서 2,058건, 추출 첨부 1,862건 |
| OCR/문서 엔진 | Tesseract `kor+eng`, PyMuPDF, Pillow, LibreOffice 확인 |
| 개인 결과 provider | **`unconfigured` (관리자 시연에서 mock 미사용)** |

Pytest에는 Starlette TestClient가 향후 `httpx2`로 이동한다는 deprecation warning 1건이 남아 있다. 현재 기능과 배포를 막는 오류는 아니다.

## 실제 화면 흐름 점검

### 1. 시작 화면 — 정상

![데스크톱 시작 화면](../../../artifacts/admin-audit-2026-08-22/01-desktop-start.png)

- SCL 홈페이지 위에 챗봇이 바로 열림
- OpenAI 연결·시연용 표시가 보임
- 빠른 질문과 개인정보 주의 문구가 첫 화면에 노출됨

### 2. 검사정보 답변 — 정상

![HPV 검사 답변](../../../artifacts/admin-audit-2026-08-22/02-hpv-answer.png)

- 공식 RDB 결과와 SCL 출처 링크 표시
- 근거가 여러 개면 확정하지 않고 범위를 설명함
- 답변 피드백 버튼 표시

### 3. 공문 답변 — 정상

![공문 답변](../../../artifacts/admin-audit-2026-08-22/03-document-answer.png)

- 문서 내용 요약과 공식 공문 링크 표시
- 검색 결과를 검사 카탈로그로 잘못 보내지 않음

### 4. 개인 결과조회 — UI·라우팅만 준비, 실제 데이터 연동 대기

![개인 결과 인증 폼](../../../artifacts/admin-audit-2026-08-22/04-result-auth-form.png)

- 별도 창 없이 채팅 안에서 인증 폼 표시
- 비밀번호와 본인확인 정보가 password 입력으로 처리됨
- 민감 결과 메시지는 sessionStorage에 저장하지 않음
- 현재 관리자 런타임은 `result_provider=unconfigured`이며 비공식 결과를 반환하지 않음
- 기관 승인 결과 Gateway가 연결되기 전에는 실제 결과 제출 시 503으로 명확히 실패함

### 5. 상담 접수 — 정상

![상담 접수 폼](../../../artifacts/admin-audit-2026-08-22/05-handoff-form.png)

- 문의 유형·이름·연락처·기관명·내용·동의를 채팅 안에서 받음
- 서버 API 및 암호화 RDB 저장은 자동 테스트로 검증
- 화면 캡처에서는 실제 개인정보를 입력하거나 접수하지 않음

## UX·접근성 판단

강점:

- 질문 입력, 결과 인증, 상담 입력에 프로그램상 label이 연결되어 있다.
- 답변 영역은 `aria-live`, 오류는 `role=alert`를 사용한다.
- 챗봇 최소화·종료·전송 버튼에 접근 가능한 이름이 있다.
- 데스크톱 화면의 reduced-motion 스타일이 있다.

확인 한계:

- 스크린샷과 DOM 확인만으로 WCAG 전체 준수를 주장할 수 없다.
- 실제 VoiceOver/NVDA 읽기 순서와 브라우저 200% 확대는 이번 범위에서 수동 검증하지 않았다.
- 운영 Gateway가 없어 실제 환자 계정과 실제 검사결과는 테스트하지 않았다.

## 배포 전 외부 입력이 필요한 항목

공개 검사·문서·위치·메뉴·상담 시연을 막는 항목은 없다. 실제 개인 결과 시연과 공개 운영 배포에는 아래 정보가 필요하다.

1. 기관 승인 결과 Gateway URL, client 자격증명, CA/mTLS 파일, 샌드박스 계정
2. 배포된 백엔드 HTTPS URL을 프런트 빌드의 `VITE_CHAT_API_URL`로 지정
3. 백엔드 `ALLOWED_ORIGINS`에 실제 프런트 도메인 지정
4. 운영용 `FIELD_ENCRYPTION_KEY`를 secret manager에서 주입
5. 원본 서버에서 이미 소실된 첨부 6건은 기관 보관본이 있을 때만 복구 가능

상담은 현재 요구사항대로 암호화 RDB 접수까지 구현되어 있으며 외부 티켓 시스템 전송은 포함하지 않는다. 사용자 피드백은 RDB에 쌓이고 FAQ 후보는 CLI 검토·게시 흐름을 사용한다.

## 관리자 시연 권장 순서

1. 시작 전 헤더의 `OpenAI 실시간 전용`, 답변 하단의 `실시간 OpenAI · RDB 근거` 표시를 확인
2. `갑상선 관련 검사 알려줘` — 실제 Structured Output 분류 + 검사 RDB 다수 후보
3. `HPV 검사 용기와 소요일` — 실제 검사·용기 상세
4. 바로 `그 검사는 무슨 용기고 며칠 걸려?` — 서버 세션 후속 질문
5. `2026년 8월 연휴 검사일정 공문` — 공개 문서 RDB 검색
6. `일반 검사의뢰서 다운로드 링크 찾아줘` — 추출 첨부/양식 RDB 검색
7. `대구의원 전화번호 알려줘` — 지점 RDB 검색
8. `검체 배송 문제로 상담원 연결해줘` — 실시간 의도 분류 + 채팅 내 실제 RDB 접수 폼

`내가 신청한 검사결과 보여줘`는 실시간 OpenAI가 인증 폼으로 라우팅하는 것까지만 보여줄 수 있다. 기관 Gateway가 연결되기 전에는 계정을 제출하지 않으며, mock 계정으로 실제 조회처럼 시연하지 않는다.
