# 리팩토링 결과 — 2026-09-16

## 범위

요청 당시 수정 중이던 PostgreSQL 이관·자연어 질의 코드를 보존하고 현재 작업 트리에 적용했다.
운영 배포, 실운영 DB 마이그레이션, 기존 데이터 삭제, 커밋은 수행하지 않았다.

## 항목별 처리

| 감사 항목 | 처리 |
| --- | --- |
| 미사용 `get_session()` | 호출자가 없는 함수와 불필요 import 삭제 |
| 미사용 파일 | 백엔드 모듈 및 홈페이지 자산은 실제 사용 중. 삭제할 파일로 잘못 분류하지 않음 |
| QA/산출물 | 루트 QA 문서를 기술 부록으로 이동, 부록 인덱스와 artifacts 보관 기준 추가. 증거 이미지·대화 로그 보존 |
| 급여코드 이중 저장 | 검색·응답 상세에서 `billing_codes`/`test_billing_codes`를 우선 사용. 원문 텍스트·상세 JSON은 수집 증거 및 링크가 없는 과거 데이터의 호환용으로 유지 |
| 분류 관계 | `GET /api/taxonomy/{term_id}/relations` 추가. 상·하위 양방향 관계, 이름, 관계 유형 제공 |
| 변경 이력 | `RevisionRepository`와 `scripts/manage_revisions.py`로 조회·보관·보존 기간 정리 지원 |
| 비어 있는 업무 테이블 | 접수·피드백·FAQ 기능이 사용하므로 유지 |
| 벡터 인덱스 | Gemini JSON 벡터 인덱스 경로로 단일화 |
| Python/Worker 중복 | `BACKEND_API_URL` 지정 시 Worker의 모든 API 요청은 Python에 전달. 백엔드 응답을 그대로 전달하고 연결 실패 시 503, 데모 답변으로 자동 전환하지 않음 |
| Worker 스키마 중복 | 런타임 CREATE TABLE/INDEX 제거. 기존 Drizzle 마이그레이션이 유일한 스키마 변경 경로 |
| 세션 무기한 보관 | API는 공유 DB 세션으로 저장, 만료 시간·보관 개수 제한·명시 삭제 지원. Worker D1도 TTL/개수 제한 적용 |
| 공급자 결합 | 공통 계획 모델·프롬프트를 `chat_contracts.py`, 검색을 `chat_retrieval.py`로 이동 |
| 거대 수집 모듈 | 공개 문서/카탈로그 수집, HTTP·파싱, DB 저장, 공통 자료형 모듈로 분리 |
| 거대 검색 모듈 | 문서·첨부·FAQ, 검사·용기·보존제·분류, 지점·경로별 검색 메서드 분리. 랭킹·캐시·상세 조회도 분리 |
| 비공개 캐시 접근 | `CatalogSearchRow`와 `search_rows()` 공개 읽기 계약 사용. 평가 스크립트·테스트도 변경 |
| 서비스 의존성 | 앱별 DB 세션 팩토리·카탈로그를 검색·챗봇·상담·분류·벡터 매핑에 주입. 커스텀 PostgreSQL 스키마 검증에도 같은 설정 적용 |
| 검증 환경 | 루트 pytest 실행 경로 추가. 단위/통합 테스트 동시 수집 시 데모 설정 충돌 제거 |

## 실행 환경별 책임

- Python API: 최신 카탈로그, 자연어 질의, 안전 정책, 상담/피드백/FAQ, 결과 제공기관 연결 담당.
- 연결된 Sites: HTTPS `BACKEND_API_URL`의 Python API에 `/api/*`와 `/health` 요청을 전달한다. 값은 경로 없는 origin이어야 하며 클라이언트에 노출되는 `VITE_` 변수로 지정하지 않는다.
- 독립 Sites 시연: 기존 공개 스냅샷·Gemini·D1 접수 기능을 보존한다. 최신 Python 검색과 완전히 동일한 답변을 약속하지 않는다. 공통 API 계약 fixture로 안전 차단·요청 검증·실시간 응답 필수 조건을 함께 검증한다.
- 공급자 공통 타입의 이전 import는 외부 스크립트 호환을 위해 재노출한다. CLI/기존 테스트용 기본 전역 객체는 유지하지만 앱 팩토리는 주입된 객체로 동작한다.

## DB 및 데이터 보관

- 새 API 테이블: `chat_sessions`. PostgreSQL 마이그레이션 `20260916_0002` 추가. 대화 이력은 입력 가림 처리 후 기록하고 공개 스냅샷 생성 시 제외한다.
- 설정: `SESSION_TTL_SECONDS=1800`, `SESSION_MAX_ENTRIES=10000`. 만료 상태는 다음 읽기에서 반환하지 않으며 활동 시 만료·초과 행을 정리한다. 트래픽이 없을 때 즉시 물리 삭제하는 스케줄러는 포함하지 않는다.
- PostgreSQL은 배포 전에 Alembic `upgrade head`를 실행해야 한다. 기존 `scl_app` 역할이 있으면 새 테이블 권한을 마이그레이션에서 부여하며, 신규 설치는 `backend/sql/runtime_roles.sql`을 적용한다.
- 이력 보존 기본 기준은 365일이며 운영 명령의 `--days`로 조정한다. 자동 삭제는 없다. 엔티티별 최신 이력은 기간이 지났더라도 보존한다.
- 조회: `python scripts/manage_revisions.py --entity-type test --entity-id 1`
- 대상 건수 확인: `python scripts/manage_revisions.py --days 365`
- 보관만 수행: `python scripts/manage_revisions.py --days 365 --archive tmp/revisions.jsonl`
- 명시적 정리: 위 명령에 `--apply` 추가. 기존 파일은 덮어쓰지 않으며 보관 내용을 디스크에 기록한 뒤 해당 이력만 삭제한다. 실행 중 오류는 DB 트랜잭션을 롤백한다. 원본 카탈로그·수집 실행·현재 원문 행은 삭제하지 않는다.
- 기록 도구는 DB를 직접 사용하는 관리자 CLI다. 공개 HTTP API에 감사 이력이나 보관·삭제 기능을 노출하지 않는다.

## 검증

최종 검증 결과:

- 백엔드: 179개 통과, PostgreSQL 통합 테스트 7개는 `TEST_DATABASE_URL` 미설정으로 건너뜀.
- 프런트엔드 상태/표시 테스트: 10개 통과.
- Sites/Worker/API 계약 테스트: 19개 통과. 실제 SQLite에 기존 Drizzle SQL을 적용한 세션 만료 검증 포함.
- Python Ruff 및 프런트엔드 ESLint 통과. Sites 프로덕션 빌드 및 필수 출력 파일 확인.
- PostgreSQL 마이그레이션은 오프라인 SQL 생성 검증 통과. 실서버 적용은 하지 않음.
- 로컬 프리뷰 HTTP 200 확인. 브라우저 시각 검증은 수행하지 않음.
- Sites 공용 빌드 래퍼는 Windows npm 경로 오류가 있어 프로젝트의 `npm run test:sites`(빌드 포함)로 검증.

재실행 명령:

- `.venv/Scripts/python.exe -m pytest`
- `.venv/Scripts/python.exe -m ruff check backend scripts`
- 프런트엔드 `npm run lint`, `npm run test:unit`, `npm run test:sites`
- PostgreSQL 마이그레이션 SQL 생성 검증. 실제 PostgreSQL 통합 테스트는 별도의 로컬 `TEST_DATABASE_URL`이 있어야 실행한다.
- 로컬 프리뷰 HTTP 응답 확인. UI 코드와 스타일은 이번 리팩토링 대상이 아니다.
