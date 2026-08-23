# 로컬 1차 완성 후 미검증 영역 점검 결과

검증일: 2026-08-20 (Asia/Seoul)  
범위: 로컬 RDB, 검색, Structured Output, 챗 오케스트레이션, API, 프런트엔드, 배포 구성  
제외: OpenAI Vector Store / File Search

## 결론

로컬에서 자동 검증 가능한 핵심 경로는 통과했다. 이번 점검에서 모델 출력의 가짜 참조를 신뢰하던 경계와 공개 문서 URL 57건의 품질 문제를 찾아 수정했다.

실제 OpenAI 키 교체 후 벡터 스토어 없이 라이브 분류 9건을 다시 실행해 9/9 통과했다. Docker 이미지 빌드는 Docker Desktop 엔진이 꺼져 있어 시작되지 않았다. 질환-검사 연결 377건은 모두 `verified=false`이므로 전문가 검토 전에는 운영 근거로 확정하면 안 된다.

## 새로 확인하고 보강한 항목

### 1. Structured Output 계약 및 신뢰 경계

- `domain`과 `sub_intent`가 동일한 분류 가지에 속하는지 Pydantic 후검증을 추가했다.
- Responses API 요청에 `store=false`를 명시했다.
- Vector Store ID가 없으면 `tools`/`file_search`가 요청에 포함되지 않음을 테스트했다.
- Structured Output 누락 시 예외가 발생하고 로컬 검색 fallback으로 전환되는 경로를 테스트했다.
- 모델이 만든 citation은 직접 노출하지 않는다. 검사코드, 문서 ID, 콘텐츠 ID, route ref가 로컬 DB에서 실제로 해석된 경우에만 인용을 생성한다.
- 존재하지 않는 검사코드·후보·문서 ID를 모델이 반환하면 모델 답변을 그대로 표시하지 않고 `no_source`로 안전하게 종료한다.

OpenAI 공식 모델 문서상 현재 설정 모델은 Responses API와 Structured Outputs를 지원한다. Structured Outputs 계약은 JSON Schema에 맞는 응답을 생성하는 방식이다.

### 2. 오케스트레이션·보안·세션

- moderation 차단 시 LLM 계획 호출이 실행되지 않는지 확인했다.
- OpenAI timeout/오류 시 로컬 검색 fallback이 사용자 응답을 반환하는지 확인했다.
- 개인 검사결과에 대한 인증·상담·의료검토 플래그가 API 응답까지 보존되는지 확인했다.
- 잘못된 세션 ID는 교체되고 이력이 설정된 최대 길이로 잘리는지 확인했다.
- 두 번째 질문에 이전 사용자/assistant 이력이 전달되는지 확인했다.
- 주민등록번호, 전화번호, 이메일 가림과 최대 입력 길이를 확인했다.
- citation URL은 HTTPS SCL 도메인과 그 하위 도메인만 허용하며 HTTP, 유사 도메인, `javascript:` URL을 거부한다.

### 3. HTTP API 경계

- `/api/search`와 `/api/tests/search`의 빈 검색어 및 500자 초과 검색어를 422로 거부한다.
- 검색 limit은 1~50, taxonomy 연결 limit은 1~200으로 검증한다.
- taxonomy ID는 1 이상으로 제한한다.
- 비활성 공개 데이터는 detail API에서도 조회되지 않는다.

### 4. 크롤링 파서와 데이터 품질

- 게시판 테이블 목록 → 상세 본문 → 첨부파일 파싱을 Mock HTTP로 재현했다.
- 카드형 게시판의 제목, 본문, 썸네일, 파일 메타데이터 파싱을 재현했다.
- 콘텐츠 URL은 다음 규칙으로 정규화한다.
  - SCL HTTP 링크는 HTTPS로 승격
  - YouTube, `youtu.be`, 네이버 블로그, 해피빈의 HTTPS 원문은 허용
  - localhost 및 알 수 없는 외부 호스트는 SCL 게시판 주소로 대체
- 실제 DB에서 57건을 정리했다.
  - SCL HTTP → HTTPS: 56건
  - localhost 개발주소 → 공식 SCL 게시판: 1건
- 깨진 문자(U+FFFD), 빈 필수값, 중복키, orphan, 외래키 위반, 허용되지 않은 URL 검사를 데이터 validator에 추가했다.

## 실행 결과

| 영역 | 결과 |
|---|---:|
| 백엔드 전체 테스트 | 52/52 통과 |
| 프런트 단위 테스트 | 5/5 통과 |
| Sites worker 테스트 | 4/4 통과 |
| 프런트 프로덕션 빌드 | 통과, 1,810 modules |
| 공개 검색 평가 | 17/17 통과 |
| 검색 Hit@K | 1.0 |
| 검색 MRR | 1.0 |
| 로컬 채팅 검색 평가 | 7/7 통과 |
| 공개 데이터 validator | 통과 |
| Python compileall | 통과 |
| Python dependency check | 통과 |
| Docker Compose 구성 문법 | 통과 |
| 실제 OpenAI 평가 9건 | 9/9 통과, Vector Store 미사용 |
| Docker 이미지 빌드 | 미실행: Docker 엔진 없음 |

백엔드 테스트에는 FastAPI TestClient의 향후 전환과 관련된 deprecation warning 1건이 있다. 현재 테스트 실패는 아니지만 의존성 갱신 시 확인이 필요하다.

## 현재 데이터 상태

- 검사 master: 2,596
- 검사 variant: 3,327
- 용기: 53
- 보존제: 53
- 양식·리플릿: 151
- 검사실 taxonomy: 13
- 지점: 66
- 사이트 route: 56
- 공지: 883
- 일반 콘텐츠: 1,024
- 첨부파일: 1,938
- 질환-검사 연결: 377 (`verified=true`: 0, `verified=false`: 377)

모든 공개 데이터셋의 최신 sync run은 `completed`이며 기존 idempotence 검사를 통과했다.

## 아직 완료로 볼 수 없는 항목

1. **질환 연결 전문가 검토**  
   377건 전부 1차 규칙 큐레이션이며 `verified=false`다. 의학·검사 전문가의 승인 결과를 저장하기 전에는 참고 후보로만 사용해야 한다.

2. **컨테이너 이미지 실행 검증**  
   Compose 문법은 유효하지만 Docker 엔진이 없어 backend/frontend 이미지 빌드와 컨테이너 health check는 수행하지 못했다.

3. **PostgreSQL 배포 경로**  
   로컬 검증은 SQLite 기준이다. PostgreSQL 연결, 스키마 생성, 쿼리 호환성, migration/backup/restore는 아직 검증하지 않았다.

4. **부하·장시간 운영**  
   동시 사용자, 세션 메모리 증가, OpenAI rate limit, timeout 분포, 비용, 크롤링 장시간 재시도는 기능 테스트 범위 밖이다.

5. **실브라우저 E2E와 접근성**  
   프런트 단위 테스트와 빌드는 통과했지만 실제 브라우저에서 backend를 함께 띄운 전체 사용자 흐름과 키보드/스크린리더 검증은 아직 없다.

## 재검증 명령

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe scripts\evaluate_openai_live.py
.\.venv\Scripts\python.exe scripts\validate_public_data.py
.\.venv\Scripts\python.exe scripts\evaluate_public_search.py

Set-Location backend
..\.venv\Scripts\python.exe -m pytest tests -q

Set-Location ..\frontend
npm run test:unit
npm run test:sites
npm run build

Set-Location ..
docker compose build
docker compose up -d
```

라이브 평가 결과에서 `vector_store_used`는 항상 `false`로 고정된다.
