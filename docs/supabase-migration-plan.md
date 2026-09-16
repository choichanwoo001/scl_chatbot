# SQLite → Supabase 마이그레이션 계획서

작성일: 2026-09-12 · 상태: 1차 Supabase 이전·로컬 API 전환·Docker DB 정리 완료 · 갱신일: 2026-09-16

### 2026-09-15 진행 현황

- 기존 프로젝트 `twvjibwtlszoyaywrxdf`의 `postgres` DB, `app` 스키마에 32개 테이블 48,108행을 이전했다.
- 전체 체크섬·제약조건 검증과 SQLite/Supabase API 7개·export 비교를 통과했다. 원본 SQLite 및 최종 백업을 보존했다.
- `.env.local`에 권한을 제한한 `scl_app` 실행 계정과 `scl_ingest` 수집 계정을 설정했고 로컬 API를 Supabase로 실행했다.
- Docker Compose는 backend·frontend만 실행하며 SQLite로 시작하지 않는다. 2026-09-16 Docker 시작 오류를 복구하고 기존 테스트 DB 컨테이너와 해당 익명 볼륨을 추가 백업 후 삭제했다.
- 공개 Worker/D1 통합과 외부 FastAPI 배포는 아직 완료하지 않았다. 아래 이전 날짜의 내용은 당시 조사·계획 기록이며 최신 실행 상태는 [실행 절차](supabase-migration-runbook.md)를 따른다.

### 2026-09-13 진행 현황

- 기존 Supabase 프로젝트를 사용하기로 결정했다. 실제 대상 프로젝트 식별 및 접속 정보는 대기 중이다.
- 단계 1~3의 백엔드 연결·Alembic·이전/검증 도구·PostgreSQL 호환 수정과 로컬 리허설을 구현했다.
- 로컬 PostgreSQL 17에 32개 테이블 48,108행을 적재했고, 정규화 체크섬 검증과 공개 데이터 무결성 검사를 통과했다.
- 단위 테스트 140개와 PostgreSQL 통합 테스트 7개 통과. 7개 API 응답 및 카탈로그 export 결과가 SQLite/PostgreSQL 간 일치했다. DDL 권한이 없는 실행 계정으로도 API·export 동작을 확인했다.
- 첨부 추출문 92행, 청크 157행의 NUL 문자는 명시적 옵션으로 U+FFFD로 바꾸며 원본은 백업에 보존한다. 나머지 타입·길이 문제는 이번 데이터에서 발견되지 않았다.
- 이번 데이터 규모에서는 전체 import를 한 트랜잭션으로 묶고 실패 시 처음부터 재실행한다. 기존 데이터가 있으면 검증만 허용해 계획의 부분 배치 재개 방식을 단순화했다.
- 아직 운영 Supabase에는 적용하지 않았고 D1/Worker, DB 세션·호출량 통합 및 운영 전환·롤백 리허설은 남아 있다.
- 구체적인 명령과 설정은 [실행 절차](supabase-migration-runbook.md)를 따른다.

## 1. 목표와 권장 방향

현재도 SQLite라는 DB를 사용하고 있다. 이번 작업의 목표는 파일 기반 SQLite와 배포용 D1에 분산된 영속 데이터를 Supabase의 관리형 PostgreSQL로 통합하는 것이다.

**권장안: 기존 FastAPI + SQLAlchemy를 유지하고, Supabase를 PostgreSQL 저장소로 사용한다. 이후 공개 화면의 API를 FastAPI로 연결해 운영 저장소를 통합한다.** 기존 검색·출처 검증·개인정보 처리 로직을 재사용할 수 있다.

1차는 백엔드 DB 이전, 2차는 배포 경로와 D1 데이터 통합으로 나눈다. 1차만 끝난 상태는 전체 서비스 이전 완료로 보지 않는다. Supabase Auth, Storage, Realtime, pgvector 도입은 후속 작업으로 분리한다. 이번 계획서 작성에서는 DB 연결 변경, 데이터 업로드, 서비스 배포를 실행하지 않았다.

## 2. 현재 구조와 확인 결과

| 영역 | 현재 구현 | 이전에 주는 영향 |
|---|---|---|
| 백엔드 DB | `backend/app/database.py`, 기본 `data/scl_catalog.db` | `DATABASE_URL`을 통해 PostgreSQL 연결 가능 |
| 드라이버·ORM | SQLAlchemy 2.x, `psycopg[binary]` 의존성 존재 | Supabase SDK로 전체 쿼리를 다시 작성할 필요 없음 |
| 스키마 | `backend/app/models.py`의 32개 테이블 | 실제 SQLite 테이블 목록과 일치. 컬럼·제약조건 일치는 추가 검증 필요 |
| 스키마 생성 | 여러 서비스 생성자에서 `init_database()` → `create_all()` | 운영에서는 버전 관리되는 migration으로 교체 필요 |
| 공개 데이터 | 검사·문서·첨부 추출문·위치·분류·수집 이력 | PK와 참조를 유지해 함께 이전 |
| 로컬 세션 | `backend/app/orchestrator.py`의 메모리 `SessionStore` | DB URL을 바꿔도 세션은 영속화되지 않음 |
| 공개 배포 코드 | Sites Worker + `catalog-data.js` + D1 | Python DB 변경만으로 배포 Worker의 조회 데이터가 바뀌지 않음 |
| D1 | `chat_sessions`, `handoff_requests`, `gemini_daily_usage` | 별도 데이터 조사와 스키마 매핑 필요 |
| 상담 암호화 | FastAPI: Fernet, Worker: AES-GCM | 암호문을 같은 컬럼에 단순 병합하면 복호화 불가 |
| SQLite 직접 접근 | `build_gemini_vector_index.py`, `export_sites_catalog.py`, `build_public_snapshot.py` | 운영 데이터 입력 경로 또는 스냅샷 생성 방식을 수정해야 함 |
| 테스트 | `backend/tests/conftest.py`에서 인메모리 SQLite 강제 | 기존 테스트 통과만으로 PostgreSQL 호환성을 증명할 수 없음 |

로컬 `data/scl_catalog.db` 조사 결과:

| 항목 | 확인값 |
|---|---:|
| DB 본체 파일 크기 | 156,020,736 bytes, 약 149 MiB |
| 애플리케이션 테이블 | 32개 |
| 검사 `tests` | 2,596건 |
| 검사 변형 `test_variants` | 3,327건 |
| 검사 상세 `test_public_details` | 3,326건 |
| 공개 문서 `public_documents` | 2,072건 |
| 첨부 `document_attachments` / 추출문 `attachment_contents` | 각각 1,938건 |
| 첨부 청크 `attachment_chunks` | 13,419건 |
| 상담 / 피드백 / FAQ 후보 | 각각 0건 |
| SQLite `foreign_key_check` | 위반 0건 |

파일 크기는 WAL·첨부 원본·벡터 파일을 포함하지 않는다. 건수는 조사 시점 참고값이며, 실제 이전 검증은 쓰기를 중지한 최종 백업에서 다시 집계한다. 로컬 상담 0건은 운영 D1에 상담이 없다는 뜻이 아니다. 운영 배포 상태, D1 데이터량, Supabase 프로젝트 및 접속 환경은 아직 확인하지 않았다.

## 3. 목표 운영 구조와 범위

```text
React 화면 → 동일 출처 API 프록시 또는 HTTPS FastAPI
                                      ↓
                          SQLAlchemy + psycopg
                                      ↓
                            Supabase PostgreSQL

크롤러·첨부 처리·벡터 인덱스 생성 → 같은 PostgreSQL
```

- FastAPI가 검색, 참조 검증, 상담·피드백, 세션, 일일 사용량을 담당한다.
- Supabase에는 기존 32개 테이블과 세션·일일 사용량 테이블을 둔다. 초기 목표는 애플리케이션 테이블 34개이며, migration 관리 테이블은 별도다.
- React의 기존 API 요청·응답 계약을 유지한다. Sites를 유지하는 경우 Worker의 API 역할을 프록시로 전환하거나 `VITE_CHAT_API_URL`로 FastAPI를 직접 지정한다. 실제 호스팅 제약 확인 후 선택한다.
- FastAPI의 별도 실행 환경이 필요하다. Supabase DB를 생성하는 것만으로 Python API가 배포되지는 않는다.
- 기존 Gemini 로컬 인덱스·OpenAI Vector Store와 ID 매핑을 유지한다. 첨부 원본과 인덱스 파일은 FastAPI 실행 환경에 제공하고, 모든 인스턴스에 같은 버전을 배포한다.
- Auth·Storage·pgvector 전환과 검색 알고리즘 개선은 별도 범위다. 공개 스냅샷은 개발·테스트용으로 유지할 수 있지만 운영의 원본은 PostgreSQL로 명시한다.

## 4. 단계별 실행 계획

### 단계 0 — 데이터와 배포 기준 확정 (0.5일)

1. 현재 사용자 트래픽이 Worker와 FastAPI 중 어디로 흐르는지 확인하고 실제 API 경로를 기록한다.
2. SQLite 최종 대상 파일, 운영 D1 내보내기 가능 여부, Supabase 개발/운영 프로젝트, FastAPI 호스팅을 확정한다.
3. 테이블별 건수·PK 범위·FK·UNIQUE·NULL·문자열 최대 길이·JSON 파싱·날짜 형식을 검사한다. SQLite가 허용한 `String(n)` 초과 값과 잘못된 타입은 PostgreSQL 적재 전에 해결한다. 묵시적 잘라내기는 금지한다.
4. SQLite backup API로 일관된 백업을 생성한다. WAL 사용 중인 `.db` 본체만 복사하지 않는다.
5. 공개 스냅샷 생성기는 상담·피드백·FAQ 후보를 제거하므로 전체 이전용 백업으로 사용하지 않는다.
6. 기존 암호화 키의 보관·복원 가능성을 확인한다. 키나 상담 원문을 로그·Git에 기록하지 않는다.

산출물: 데이터 목록, 타입 변환 규칙, 백업 위치와 체크섬, 현재/목표 배포 경로. 완료 기준: 이전 대상과 보존할 데이터가 모두 식별됨.

### 단계 1 — Supabase 연결과 스키마 관리 (1일)

1. 개발/검증 환경과 운영 환경을 분리한다. API와 DB 간 지연을 줄일 리전과 용량을 선택한다. 요금·백업 보존·복구 기능은 실제 프로젝트 조건으로 확인한다.
2. 상시 실행 FastAPI는 Direct connection을 우선 검토하고, IPv4 환경이면 Session pooler를 사용한다. Supabase Connect 화면의 실제 주소를 사용한다. Direct/Session과 Transaction pooler의 용도 및 제약은 [공식 연결 문서](https://supabase.com/docs/guides/database/connecting-to-postgres)를 따른다.
3. `postgresql+psycopg://...` 형태의 `DATABASE_URL`과 별도 migration용 연결 설정을 마련한다. SSL을 적용하고 운영에서는 CA 설정과 서버 인증서 검증까지 확인한다. URL·비밀번호는 서버 비밀 설정으로만 주입한다.
4. `pool_size`, `max_overflow`, 연결·쿼리 제한 시간을 설정한다. API 프로세스 수 × 프로세스별 최대 연결 수에 크롤러·배치·관리 연결을 더해 프로젝트 한도 안에 둔다.
5. Transaction pooler가 필요한 배포에서만 별도 검증한다. psycopg 자동 prepared statement는 해당 모드에서 `prepare_threshold=None`으로 비활성화한다. [공식 드라이버 설정](https://supabase.com/docs/guides/troubleshooting/disabling-prepared-statements-qL8lEL)
6. Alembic을 도입해 현재 모델 기반 초기 migration을 만든다. 자동 생성 결과는 FK·인덱스·기본값까지 검토한다. 자동 생성은 검토가 필요한 초안이다. [Alembic 공식 문서](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)
7. 애플리케이션 전용 `app` 스키마를 권장한다. 모델·FK·raw SQL·migration의 스키마 해석을 통일하고 해당 스키마를 Data API 노출 대상에서 제외한다. Alembic 비교 대상도 애플리케이션 스키마로 제한해 Supabase의 `auth`·`storage` 등을 변경하지 않는다.
8. 이 프로젝트의 애플리케이션 DDL은 Alembic 한 곳에서 관리한다. Drizzle/Supabase CLI와 같은 테이블의 변경 이력을 이중 관리하지 않는다.
9. 운영 시작 시 `create_all()`과 demo seed를 실행하지 않도록 모든 호출부를 정리한다. 배포 단계에서 migration을 먼저 적용하고 API는 스키마 버전을 검사한다. 운영 `DATABASE_URL` 누락 시 SQLite로 조용히 전환하지 않고 시작 실패로 처리한다.

권한 정책: migration 계정과 애플리케이션 계정을 분리하고, 실행 계정에는 필요한 테이블·시퀀스 권한만 준다. 전용 스키마의 CREATE 권한은 제한한다. Data API를 사용하지 않으면 비활성화하고, `anon`·`authenticated`의 불필요한 권한과 기본 권한을 제거한다. 노출 스키마에 테이블을 둘 경우 RLS와 grants를 함께 설정한다. RLS가 직접 연결 계정의 권한이나 서버 측 인가를 대신하지는 않는다. [API 보안](https://supabase.com/docs/guides/api/securing-your-api), [RLS](https://supabase.com/docs/guides/database/postgres/row-level-security)

산출물: Alembic 초기 migration, 서버 연결 설정, 역할·권한 설정. 완료 기준: 빈 검증 DB에서 재현 가능한 스키마 생성 및 제한된 실행 계정의 정상 조회·쓰기.

### 단계 2 — SQLite 데이터 이전 도구와 리허설 (1~1.5일)

1. `scripts/migrate_sqlite_to_postgres.py`를 새로 작성한다. 읽기 전용 원본, 대상 URL, dry-run, 배치 크기, 재시작 시 검증 옵션을 제공한다.
2. `Base.metadata.sorted_tables` 등 FK 의존 순서에 따라 스키마와 타입을 명시한 배치 적재를 수행한다. SQLite SQL dump를 그대로 PostgreSQL에 실행하지 않는다.
3. PK, FK, 공개 식별자, `local_ref`, 수집 이력과 암호문을 보존한다. 예를 들어 `document:123`이 이전 전후 같은 문서를 가리켜야 한다.
4. Boolean은 검증된 0/1을 변환하고, JSON 문자열은 객체/배열로 파싱한다. 초기에는 기존 JSON 의미를 유지하며 JSONB 최적화는 별도로 판단한다. 날짜는 UTC로 정규화하되 시간대 없는 값이 UTC였는지 생성 경로를 확인한 후 변환한다.
5. ORM의 Python `default`/`onupdate`와 DB `server_default`가 다름을 고려한다. import가 필요한 값을 명시하고, 원시 SQL 쓰기에 필요한 기본값은 migration에서 정의한다.
6. 명시적 정수 ID 적재 후 각 자동 증가 시퀀스를 실제 최대 ID에 맞춘다. 빈 테이블도 다음 INSERT가 정상인지 검사한다.
7. 초기 적재는 빈 대상에서만 실행한다. 실패한 배치는 롤백하고, 재실행 시 완료 배치의 PK·체크섬을 검증한다. 운영 데이터가 있는 대상에는 전체 삭제나 무조건 upsert를 하지 않는다.
8. `scripts/verify_database_migration.py`를 새로 작성해 테이블별 건수, PK 집합, 타입 정규화 후 행 체크섬, FK 고아 레코드, UNIQUE·NULL을 비교한다. 적재 후 통계를 갱신하고 주요 쿼리 계획을 확인한다.
9. 검증 Supabase에서 전체 이전을 실행해 소요 시간·실패 원인·DB 사용량을 기록한다. 이 결과로 운영 중지 시간을 산정한다.

산출물: 적재/검증 스크립트, 리허설 보고서. 완료 기준: 기존 32개 테이블의 데이터 차이 0건, 의도한 변환만 존재, 신규 INSERT 성공.

### 단계 3 — 실행 코드·스크립트·테스트 호환성 (1일)

- `database.py`와 설정에 운영 연결 정책을 적용하고 서비스 생성 시 DDL 의존을 제거한다.
- `build_gemini_vector_index.py`와 `export_sites_catalog.py`가 `DATABASE_URL`을 통해 같은 원본을 읽게 한다. `verified = 1` 같은 Boolean SQL, `LIKE` 대소문자 처리, `substr`, NULL 정렬, GROUP BY 차이를 검토한다.
- `build_public_snapshot.py`는 개발용 SQLite 입력 전용임을 명시하거나 PostgreSQL에서 비공개 데이터를 제외해 SQLite 스냅샷을 만드는 별도 export를 추가한다. 운영 배치가 오래된 로컬 파일을 읽는 경로를 제거한다.
- `validate_public_data.py`의 고정 EXPECTED 값은 공개 데이터셋 회귀 검사로 구분한다. 이전 검증은 최종 원본 manifest를 기준으로 한다. 현재 PostgreSQL에서는 생략되는 FK 점검을 실제 검증 쿼리로 보완한다.
- 카탈로그 캐시의 초기 적재 시간·갱신 감지·여러 프로세스 간 최신성, 원격 DB 왕복 횟수를 확인한다.
- SQLite 단위 테스트는 유지하고 PostgreSQL 통합 테스트 실행 경로를 추가한다. `conftest.py`가 통합 DB URL을 덮어쓰지 않도록 분리한다. 테스트 대상은 운영 DB가 아닌 격리된 DB/스키마로 제한한다.
- 크롤러 재실행의 멱등성, FK/UNIQUE 위반 처리, 상담/피드백 저장과 복호화, 검색 결과·출처 참조를 검증한다. 외부 LLM 호출은 모킹해 DB 검사에서 API 비용이 발생하지 않게 한다.

산출물: PostgreSQL 호환 코드와 통합 테스트, 스크립트 입력 경로 정리. 완료 기준: SQLite 단위 테스트와 PostgreSQL 통합 테스트 모두 통과.

### 단계 4 — 공개 배포 경로와 D1 통합 (1.5~2.5일)

이 단계는 실제 Worker 사용이 확인되면 전체 이전에 필수다. Worker를 계속 주 API로 유지해야 한다면 별도 설계가 필요하며, 단순한 Drizzle dialect 변경만으로 D1 binding과 Worker SQL을 전환할 수 없다.

| D1 데이터 | 목표와 변환 규칙 |
|---|---|
| `chat_sessions` | 새 PostgreSQL 세션 테이블로 이전. `session_id` 보존, `last_test_json`의 코드/변형을 현재 카탈로그에서 검증하고 최소 상태만 변환 |
| `handoff_requests` | 기존 백엔드 테이블에 통합. D1에는 없는 정수 `id`는 신규 부여, `public_id`는 유지. `related_refs_json` → `related_refs`, 날짜 → UTC |
| `gemini_daily_usage` | 새 사용량 테이블로 이전. 기존 UTC 일자 기준을 유지하고 일자별 호출량을 보존 |

1. Worker 계약 테스트를 기준으로 모든 UI API를 FastAPI에서 검증한다. `frontend/src/lib/chatApi.js`, Worker 라우팅, 빌드 환경변수, CORS/동일 출처 프록시를 함께 조정한다. 브라우저에 DB 비밀번호나 Supabase 관리 키를 넣지 않는다.
2. FastAPI `SessionStore`를 DB 기반으로 교체한다. 직전 검사 코드·변형 키와 필요한 최소 문맥을 저장하고, TTL·삭제 API·동시 업데이트 정책을 적용한다. D1에 없던 과거 대화 이력을 만들어 넣지 않는다. 새로 영속화하는 대화는 개인정보 마스킹과 보존 기간을 명시한다.
3. Worker의 호출량 예약 동작을 백엔드에 구현한다. 현재 백엔드의 `GEMINI_DAILY_REQUEST_LIMIT` 설정만으로는 Worker의 D1 기반 제한이 재현되지 않는다. 호출 직전 조건부 원자 갱신을 적용하고, 임베딩·답변 생성 등 기존 제한 대상과 실패 시 차감 규칙을 맞춘다.
4. 상담 암호문은 통제된 이전 작업에서 기존 `HANDOFF_ENCRYPTION_KEY`로 AES-GCM 복호화한 뒤 백엔드 `FIELD_ENCRYPTION_KEY`로 Fernet 재암호화한다. 평문은 메모리에서만 처리하고 출력하지 않는다. 검증 성공/실패 건수만 기록한다. 키 확보 전에는 해당 데이터 병합을 진행하지 않는다.
5. `public_id` 중복 시 내용 일치를 확인한다. 서로 다른 상담을 임의 덮어쓰지 않고 충돌 보고서를 만든다. 사용량과 세션 재적재도 중복 증가·덮어쓰기 방지 규칙을 둔다.
6. FastAPI를 배포하고 공개 화면을 연결한다. Worker의 D1·정적 카탈로그 조회 경로가 운영 요청에서 제거됐는지 확인한다. Supabase의 공개 데이터 변경이 재빌드 없이 API와 화면에 반영되는지 검사한다.
7. 전환 기간에는 이전 Worker/D1을 복구용으로 보존한다. 안정화와 복구 리허설 완료 후 불필요한 D1 binding·Drizzle migration·배포 스냅샷 의존을 정리한다.

산출물: 통합 API 배포, D1 변환 보고서, 세션·사용량 저장소. 완료 기준: 공개 화면의 모든 운영 읽기·쓰기가 목표 경로를 사용하고 기존 상담·세션·호출량이 보존됨.

## 5. 운영 전환과 롤백

짧은 쓰기 중단을 허용하는 전환을 기본안으로 한다. 사용자 규모와 허용 중단 시간은 착수 시 확정한다. 무중단이 필수라면 변경분 수집과 재적용 설계를 추가하고 일정을 다시 산정한다.

1. 검증 환경에서 migration, 전체 적재, D1 변환, 검증, API 전환, 복구를 한 번 이상 리허설한다.
2. 운영 전환 시 SQLite와 D1에 쓰는 크롤러·상담·피드백·세션·사용량 기록을 중지한다. 진행 중 요청을 비운다. 채팅도 세션과 사용량을 쓰므로 읽기 전용으로 취급하지 않는다.
3. 최종 일관 백업과 원본 manifest를 만든다. 검증용 DB와 별개인 운영 대상으로 최종 데이터를 적재한다.
4. 건수·체크섬·FK·암호화·시퀀스 검증을 통과한 뒤 API 설정과 프런트 경로를 전환한다.
5. 제한된 점검 요청으로 검색·상담·세션 삭제·호출 한도를 확인하고 쓰기를 재개한다. 점검 데이터는 식별 가능하게 관리한다.
6. 초기 24~48시간 동안 DB 연결 오류·풀 대기·응답 지연·상담 저장 실패·출처 불일치·사용량 초과를 관찰한다. 백업을 복구할 수 있는지도 확인한다.

롤백 기준: 데이터 차이 또는 복호화 실패 1건 이상, 핵심 API 실패, 반복되는 DB 연결 오류, 합의된 지연 기준 초과.

- **새 운영 쓰기 재개 전:** 기존 API/Worker와 동결한 SQLite/D1으로 되돌릴 수 있다.
- **새 운영 쓰기 재개 후:** URL만 되돌리면 새 상담·세션·사용량을 잃는다. 우선 쓰기를 중지하고 PostgreSQL 전체 백업 및 변경분을 확보한다. SQLite/D1 역변환을 리허설한 경우에만 기존 저장소로 복귀한다. 이때 상담은 필요 시 Fernet → AES-GCM 재암호화, 사용량·세션·삭제 이력도 반영한다.
- 역변환이 준비되지 않았으면 PostgreSQL 데이터를 유지한 채 호환되는 이전 앱 버전을 배포하거나 수정 배포한다. 초기 기간의 변경분 식별을 위해 감사 기록 또는 변경 저널을 마련하고 단순 `created_at` 조회로 UPDATE/DELETE까지 복구할 수 있다고 가정하지 않는다.
- 백업 보존 기간과 폐기 시점은 운영 정책으로 정하며, 안정화 전에 기존 DB·키를 삭제하지 않는다.

## 6. 완료 판정 기준

- [ ] 최종 원본 대비 모든 이전 대상의 건수·PK·정규화 체크섬 일치. 의도한 제외/변환은 별도 명세로 설명 가능.
- [ ] FK 고아 레코드·새로운 중복·필수값 누락 0건. 전체 제약조건이 활성 상태이고 자동 증가 INSERT 성공.
- [ ] 대표 검사 검색·검사 상세·문서·첨부·FAQ·출처 검증 결과 일치. 테스트 코드와 변형 키 보존.
- [ ] 신규 상담/피드백 저장과 기존 상담 복호화 검증 성공. API/로그에 상담 평문이 노출되지 않음.
- [ ] 세션의 인스턴스 간 유지·재시작 후 복원·삭제·TTL 및 호출량 원자적 제한 검증 성공.
- [ ] PostgreSQL 통합 테스트와 프런트 계약 테스트 통과. SQLite 단위 테스트도 회귀 없음.
- [ ] 프로젝트 대표 부하를 명시하고 전후 비교. 초기 제안 기준은 DB 의존 API p95 악화 20% 이내, 예상 동시 요청에서 풀 고갈 0건이며 리허설 후 확정.
- [ ] 익명/일반 사용자 경로에서 비공개 테이블 접근 불가, 앱 계정으로 DDL 불가, 정상 API의 필요한 쓰기는 허용.
- [ ] 공개 화면 요청이 실제 Supabase 연결 FastAPI를 사용하고, 운영 수집 스크립트도 동일 DB를 사용.
- [ ] 백업 복원과 쓰기 재개 이후의 롤백 절차까지 리허설 완료.

## 7. 변경 파일과 작업 단위

아래 신규 파일은 구현 예정이며 현재 생성된 것으로 간주하지 않는다.

| 작업 단위 | 주요 파일/산출물 |
|---|---|
| PR 1: 연결·migration | `backend/app/database.py`, `backend/app/config.py`, `backend/app/models.py`, 서비스의 `init_database()` 호출부, `backend/requirements.txt`, `.env.example`, 신규 `backend/alembic.ini`, `backend/alembic/` |
| PR 2: 데이터 도구 | 신규 `scripts/migrate_sqlite_to_postgres.py`, `scripts/verify_database_migration.py`, `scripts/validate_public_data.py` |
| PR 3: 운영 경로 호환 | `scripts/build_gemini_vector_index.py`, `scripts/export_sites_catalog.py`, `scripts/build_public_snapshot.py`, PostgreSQL 통합 테스트 |
| PR 4: D1·API 통합 | `backend/app/orchestrator.py`, `backend/app/gemini_gateway.py`, 세션·사용량 모델/저장소, D1 이전 도구, `frontend/worker/index.js`, `frontend/src/lib/chatApi.js`, 프런트 빌드·환경 설정 |
| 운영 전환 | `docker-compose.yml`, 배포 환경 설정, `README.md`, `docs/architecture.md`, `docs/operations-and-handoff.md`, `docs/data-snapshot.md`, RDB 원본 위치 변경 ADR |

현재 작업 트리에는 LLM 실행 설정 등의 미커밋 변경이 있다. 구현 착수 시 해당 변경과 충돌 여부를 확인하고 기존 작업을 보존한다.

## 8. 예상 일정과 착수 시 확정할 사항

1인 개발 기준, 기존 FastAPI를 재사용하고 짧은 쓰기 중단이 가능한 경우의 예상이다.

| 범위 | 예상 작업 시간 |
|---|---:|
| 백엔드 DB 이전: 단계 0~3 | 3.5~4일 |
| Worker/D1 통합: 단계 4 | 1.5~2.5일 |
| 운영 전환·복구 점검 | 0.5~1일 |
| 전체 | 약 6~8 작업일 + 24~48시간 관찰 |

계정/인프라 준비 대기, 데이터 정제, 신규 호스팅 문제, 무중단 전환은 추가 시간이 필요하다. 실제 DB 적재 시간과 서비스 중지 시간은 개발 일정과 별개이며 리허설에서 측정한다.

착수 시에는 Supabase 개발/운영 프로젝트와 리전, FastAPI 배포 위치, 현재 D1 운영 데이터와 암호화 키의 보존 상태, 허용 중단 시간, 세션 보존 기간을 확정한다. 기본 실행 순서는 **검증 Supabase 준비 → Alembic → SQLite 이전 리허설 → PostgreSQL 회귀 검사 → D1/배포 통합 → 최종 전환·관찰**이다.
