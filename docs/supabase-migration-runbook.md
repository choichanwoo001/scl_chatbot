# Supabase 이전 실행 절차

작성일: 2026-09-13 · 갱신일: 2026-09-16. 기존 Supabase 프로젝트에 1차 백엔드 데이터 이전과 로컬 API 연결 전환을 완료했다. 공개 Worker/D1 배포 전환은 후속 범위다.

## 2026-09-15 실행 상태

- 기존 Supabase 프로젝트 `twvjibwtlszoyaywrxdf`를 대시보드에서 확인했다. DB 이름은 `postgres`, Session pooler 호스트는 `aws-0-ap-southeast-1.pooler.supabase.com:5432`이다.
- 대상의 기존 `app`·`public` 테이블 0개를 확인한 뒤 Alembic `20260913_0001`을 적용했다. `app`에 업무 테이블 32개와 버전 테이블 1개가 있다.
- 최종 전체 백업 `tmp/migration/20260915-cutover-source.db`에서 48,108행을 적재했다. 전환 직전 원본을 다시 검사해 백업 이후 변경이 없음을 확인했다. 원본 SQLite와 백업은 보존했다.
- `supabase-import.json`, `supabase-verification.json`에서 모든 테이블의 행 수·정규화 체크섬·제약조건 검증을 통과했다. 보고서는 `tmp/migration/`에 있다. 추출문·청크의 명시적 NUL 치환은 기존 리허설과 동일하다.
- 실제 Supabase의 `scl_app` 계정으로 API 7개와 카탈로그 export를 실행해 SQLite 결과와 일치함을 확인했다. 벡터 입력 3,933건도 일치한다. 신규 임베딩 API 호출은 하지 않았다. 보고서: `tmp/migration/supabase-runtime-validation.json`.
- `.env`의 `DATABASE_URL`은 `scl_app`, `INGEST_DATABASE_URL`은 `scl_ingest`로 설정했다. 두 역할은 DDL 권한이 없고, 필요한 DML 권한만 있는 것을 확인했다. 기존 Fernet 키를 유지하고 `APP_ENV=production`으로 전환했다.
- 로컬 API를 `127.0.0.1:8000`에서 실행하고 health·카탈로그 상태·HPV 검색 HTTP 200을 확인했다. PID 기록은 `tmp/migration/supabase-api.pid`이다.
- Supabase Table Editor의 `app → test_variants`에 실제 3,327건이 표시되는 것을 확인했다.
- Compose 서비스는 backend·frontend뿐이다. Docker backend는 production 검증을 강제해 SQLite로 시작하지 않게 했다.
- Compose 설정 검증을 통과했다. 실행 컨테이너에 migration·ingest URL과 DB 관리자 비밀번호를 전달하지 않고, `.env`와 SQLite 압축 snapshot을 이미지 빌드에서 제외했다.
- 2026-09-16 Docker Desktop 시작 오류를 복구했다. 종료 후 `Docker/run`과 `docker-secrets-engine` 소켓 폴더를 각각 `.pre-supabase-20260916-011113` 백업 이름으로 옮겼고, 기존 `settings-store.json`은 `tmp/migration/docker-settings-before-repair.json`에 보존했다. `EnableDockerAI=false`로 변경한 뒤 엔진 정상 시작을 확인했다. Docker AI 기능은 현재 꺼져 있다. [동일한 오류 보고](https://github.com/docker/desktop-feedback/issues/531).
- 리허설 컨테이너 `scl-postgres-migration-test`의 라벨과 단일 익명 데이터 볼륨을 확인했다. `tmp/migration/docker-postgres-20260916.dump`에 27,362,379바이트의 PostgreSQL 백업을 만들고 `pg_restore --list` 검증 후 컨테이너와 해당 볼륨을 삭제했다. 둘 다 목록에 없는 것을 확인했다. 백업 해시·삭제 결과: `tmp/migration/docker-removal-backup.json`. 다른 컨테이너·볼륨은 정리하지 않았다.

## 구현 범위

- 기존 32개 테이블의 Alembic 초기 migration, 별도 `app` 스키마.
- PostgreSQL 런타임은 스키마 버전만 검사하며 DDL을 실행하지 않는다. SQLite 개발 환경은 기존 bootstrap을 유지한다.
- `APP_ENV=production`에서 SQLite·demo seed·암호화 키 누락·비암호화 PostgreSQL 연결을 거부한다.
- Direct/Session/Transaction pool 연결 설정과 트랜잭션별 search_path·statement timeout, SQL 파라미터 로그 숨김.
- SQLite 읽기 전용 접근, WAL을 포함한 전체 backup, 데이터 타입 검사, 빈 PostgreSQL로의 원자적 적재, 체크섬 검증, 자동 증가 시퀀스 복원.
- 카탈로그 export와 Gemini 인덱스 입력은 `DATABASE_URL`을 사용한다. 이 단계에서 Gemini API를 호출하거나 인덱스를 재생성하지 않는다.
- 벡터 빌더는 실제 embedding 입력의 해시도 확인한다. 기존 인덱스에 이 해시가 없으면 다음 명시적 빌드에서 재임베딩 대상이 되므로, 실행 전 호출량·비용을 확인한다.
- D1의 상담·세션·호출량 통합과 공개 Worker 전환은 아직 적용하지 않았다. FastAPI 세션도 현재는 메모리 기반이므로 공개 배포 경로 전환은 해당 후속 단계 완료 후 진행한다.

## 연결 설정

저장소 루트에서 실행한다. 모든 로컬 설정과 비밀값은 Git에서 제외된 루트 `.env` 하나에 두고, 배포값은 호스팅 환경의 비밀 설정에 둔다. `SCL_SKIP_LOCAL_ENV=true`는 테스트에서 루트 `.env` 로드를 건너뛴다.

Connect → Session pooler에서 확인한 프로젝트 ID·호스트와 DB 비밀번호를 아래처럼 `.env`에 넣으면 연결 문자열의 비밀번호 인코딩을 자동 처리할 수 있다. API key가 아닌 Database password를 사용한다.

```dotenv
SUPABASE_PROJECT_REF=PROJECT_REF
SUPABASE_DB_HOST=EXACT_POOLER_HOST.pooler.supabase.com
SUPABASE_DB_PASSWORD='DATABASE_PASSWORD'
```

```powershell
.venv/Scripts/python.exe scripts/configure_supabase_connection.py
```

이 명령은 `MIGRATION_DATABASE_URL`과 스키마·pool 설정만 준비한다. 데이터 적재나 API의 `DATABASE_URL` 전환은 하지 않는다.

Docker Compose는 backend·frontend만 실행하고 DB 컨테이너를 만들지 않는다. backend는 `APP_ENV=production`으로 실행되어 DB 설정 누락 시 SQLite로 시작하지 않는다. 검증을 마친 Supabase `DATABASE_URL`과 기존 `FIELD_ENCRYPTION_KEY`를 `.env`에 설정한 뒤 `docker compose up -d --build`로 시작한다. `data` 마운트는 첨부파일·인덱스 보존용으로 유지한다.

```dotenv
APP_ENV=development
DATABASE_SCHEMA=app
DATABASE_POOL_MODE=session

# 기존 Supabase Connect 화면의 정확한 Session pooler 주소 사용.
# PASSWORD는 URL 인코딩된 값, PROJECT_REF/POOLER_HOST는 실제 프로젝트 값.
MIGRATION_DATABASE_URL=postgresql+psycopg://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:5432/postgres?sslmode=require

# 권한 준비 후 API가 사용할 별도 실행 계정.
DATABASE_URL=postgresql+psycopg://scl_app.PROJECT_REF:PASSWORD@POOLER_HOST:5432/postgres?sslmode=require
FIELD_ENCRYPTION_KEY=EXISTING_FERNET_KEY
SEED_DEMO_ON_EMPTY=false
```

초기 migration/적재 명령만 실행할 때는 `DATABASE_URL`을 기존 SQLite로 유지해도 된다. 대상은 명시적 `MIGRATION_DATABASE_URL`로 지정한다. 운영 API에는 migration 계정 비밀을 주입하지 않는다. SSL `require`는 전송 암호화만 보장하므로 운영에서는 CA와 `verify-full` 설정도 확인한다. 연결 모드별 주소·IPv4 지원·SSL 설정은 [Supabase 공식 연결 문서](https://supabase.com/docs/guides/database/connecting-to-postgres)를 따른다.

기존 FastAPI 상담을 보존하려면 기존 Fernet 키를 그대로 사용한다. 로컬 자동 생성 키의 위치는 `data/.field-encryption.key`이다. **Worker의 `HANDOFF_ENCRYPTION_KEY`는 AES-GCM용이며 Fernet 키와 호환되지 않는다.** 이 단계의 도구로 D1 암호문을 직접 병합하지 않는다.

## 스키마와 역할 준비

기존 프로젝트의 `app` 스키마 사용 여부와 백업 상태를 먼저 확인한다. 이 프로젝트의 기존 스키마가 있다면 초기 migration을 강제 stamp하거나 기존 테이블을 지우지 않는다. 적용 이력과 실제 스키마를 먼저 대조한다.

```powershell
.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python.exe -m alembic -c backend/alembic.ini check
```

Alembic은 `app`만 관리하며 `auth`·`storage`·기존 `public` 테이블을 변경하지 않는다. 초기 migration은 비공개 스키마에서 PUBLIC 및 기존 Supabase API 역할의 권한을 제거한다. Supabase API 설정에서 `app`을 exposed schemas에 추가하지 않는다. Data API가 불필요한 프로젝트는 비활성화할 수 있다. [Supabase API 보안](https://supabase.com/docs/guides/api/securing-your-api)

`backend/sql/runtime_roles.sql`을 검토한 뒤 migration 소유자로 실행한다. 기본 `app` 스키마용이며 사용자 지정 스키마를 쓴다면 SQL도 맞춘다.

- `scl_app`: 애플리케이션 테이블 조회, 상담·피드백·FAQ 쓰기, 시퀀스 사용.
- `scl_ingest`: 수집·관리 배치용 DML. migration 버전 변경 권한은 없음.
- 역할의 비밀번호는 Supabase 관리 화면 또는 안전한 관리 연결에서 따로 설정한다. SQL 파일에는 저장하지 않는다.
- 같은 이름의 기존 역할이 있다면 상속/멤버십과 관리자 권한을 먼저 점검한다. 스크립트는 기존 역할의 다른 권한을 임의로 제거하지 않는다.
- API의 `DATABASE_URL`에는 `scl_app`, 수집 프로세스에는 `scl_ingest` 계정을 사용한다. 둘 다 DDL은 Alembic에 맡긴다.
- 새 테이블을 추가할 때 실행 계정에 필요한 쓰기 권한도 해당 migration에서 명시한다.

## 백업과 사전 검사

```powershell
.venv/Scripts/python.exe scripts/migrate_sqlite_to_postgres.py `
  --source data/scl_catalog.db `
  --backup tmp/migration/final-source.db `
  --dry-run --assume-naive-utc --replace-text-nuls `
  --report tmp/migration/preflight.json
```

- backup 파일은 새 경로여야 한다. 기존 파일과 원본을 덮어쓰지 않는다.
- dry-run은 대상 PostgreSQL에 연결하지 않는다.
- `--assume-naive-utc`: 기존 코드의 UTC 생성 시각이 SQLite에 offset 없이 저장된 데이터임을 확인하고 사용하는 옵션이다. 다른 출처의 날짜에 무조건 적용하지 않는다.
- `--replace-text-nuls`: `attachment_contents.extracted_text`와 `attachment_chunks.text`에 한해 U+0000을 U+FFFD로 치환한다. 기본 동작은 거부이며, 옵션을 명시해야 적용한다. 원본 DB와 원본 다운로드 파일은 보존된다.
- 이번 로컬 조사에서는 추출문 92행/1,692문자, 청크 157행/1,825문자가 해당했다. 실제 대상에서는 보고서의 변환 건수를 다시 확인한다.
- 다른 타입 오류, 길이 초과, SQL NULL, FK 위반은 자동 삭제/잘라내기 없이 실패 처리한다.
- 이 도구의 전체 백업에는 비공개 데이터가 포함될 수 있다. Git에 추가하지 않는다. 공개용 `build_public_snapshot.py`는 비공개 행을 제거하므로 전체 이전 백업으로 사용할 수 없다.

## 실제 적재와 검증

운영 전환에서는 크롤러뿐 아니라 상담·피드백·세션·사용량 기록 등 모든 쓰기를 멈추고 최종 백업을 만든 후 실행한다. 그 전에 별도 검증 환경에서 아래 과정을 리허설한다.

```powershell
.venv/Scripts/python.exe scripts/migrate_sqlite_to_postgres.py `
  --source tmp/migration/final-source.db `
  --apply --assume-naive-utc --replace-text-nuls `
  --report tmp/migration/import.json

.venv/Scripts/python.exe scripts/verify_database_migration.py `
  --source tmp/migration/final-source.db `
  --assume-naive-utc --replace-text-nuls `
  --report tmp/migration/verification.json
```

적재는 빈 대상에만 가능하다. FK 순서에 따라 500행씩 전송하되 전체 import는 한 트랜잭션으로 묶는다. 대상 테이블의 다른 쓰기를 잠그고, 검증 실패 시 전체 행 변경을 롤백한다. 149MiB 규모의 이번 데이터에서는 부분 배치 재개보다 이 방식이 단순하고 안전해 계획서의 배치 checkpoint 방식을 대체했다. 중단 후에는 빈 대상에 처음부터 재실행한다. PostgreSQL 시퀀스 값은 트랜잭션 롤백 대상이 아니지만 재적재 완료 시 다시 맞춰지며, 실패로 생긴 번호 간격은 데이터 손실이 아니다.

이전이 이미 끝났는지만 확인하려면 `--apply` 대신 `--verify-existing`을 사용한다. 기존 내용이 완전히 일치해야 성공하며 UPDATE/DELETE/upsert를 수행하지 않는다. 이 명령도 잠금을 사용하므로 서비스 재개 후 정기 검증에는 별도 `verify_database_migration.py`를 사용한다.

검증은 테이블별 행 수, 정렬된 PK 체크섬, JSON·UTC·명시한 문자 변환을 정규화한 행 체크섬, 컬럼·PK·FK·UNIQUE·NULL 제약과 FK 고아 행을 검사한다. 보고서에는 원문·암호문·키·DB URL을 넣지 않는다. CLI는 DB 드라이버의 비밀값 포함 가능성이 있는 오류 본문을 출력하지 않는다.

```powershell
$env:PYTHONPATH = 'backend'
.venv/Scripts/python.exe scripts/validate_public_data.py
```

이 명령의 고정 EXPECTED 건수는 기존 공개 데이터셋 회귀 기준이다. 수집 데이터가 바뀌면 최신 manifest 비교와 구분해서 해석한다. PostgreSQL FK 검증도 실제로 실행한다.

## 로컬 테스트

```powershell
$env:PYTHONPATH = 'backend'
.venv/Scripts/python.exe -m pytest backend/tests -q

# TEST_DATABASE_URL은 별도 로컬 PostgreSQL DB의 연결 문자열로 설정한다.
# DB 이름은 _test로 끝나야 하고 localhost/127.0.0.1/::1만 허용한다.
.venv/Scripts/python.exe -m pytest backend/integration_tests -q
```

두 테스트 디렉터리는 별도 명령으로 실행한다. 단위 테스트는 인메모리 SQLite와 demo fixture를 사용하고, 통합 테스트는 임의의 `test_<UUID>` 스키마에서 Alembic을 실행한 뒤 그 스키마만 정리한다. `TEST_DATABASE_URL`이 없으면 통합 테스트는 skip된다. 운영/원격 DB 주소를 테스트에 넣지 않는다.

## 전환과 남은 작업

운영에서는 `APP_ENV=production`, 실행 계정의 `DATABASE_URL`, 기존 Fernet 키를 설정하고 API를 재시작한다. `DATABASE_URL`을 바꿔도 기존 프로세스의 engine이 자동 갱신되지는 않는다. API 인스턴스 수에 따른 연결 풀 총량과 캐시 초기화 시간을 확인한다.

1차 Supabase import·검증·역할·SSL 연결·로컬 API 전환은 완료했다. 후속으로 D1 데이터 export·암호문 변환, DB 세션/일일 사용량 구현, FastAPI 호스팅, 공개 Worker 경로 전환을 진행한다. 이 단계들이 끝나기 전에는 전체 서비스 이전 완료로 판정하지 않는다. 수집 배치를 실행할 때는 저장한 `INGEST_DATABASE_URL`을 해당 프로세스의 `DATABASE_URL`로 사용한다. 로컬 파일의 API 계정 설정이 덮어쓰지 않도록 필요한 환경변수를 주입하고 `SCL_SKIP_LOCAL_ENV=true`를 사용한다.

쓰기 재개 전 실패는 기존 API/SQLite로 복귀할 수 있다. 쓰기 재개 후에는 새 데이터를 먼저 백업하고, 새 PostgreSQL을 유지한 채 호환되는 앱을 수정/복원하는 것을 우선한다. DB URL만 되돌리거나 초기 migration downgrade로 테이블을 제거하지 않는다. 초기 revision의 downgrade는 의도적으로 차단되어 있다. D1 포함 역변환 리허설은 후속 단계다.

## 2026-09-16 세션 저장 변경

API 배포 전 `alembic upgrade head`로 `20260916_0002`를 적용한다.
신규 `chat_sessions` 테이블은 대화 세션 공유·만료에 사용하며 공개 스냅샷에서 제외된다.
기존 `scl_app` 권한은 마이그레이션이 추가한다. 신규 역할 설치는 갱신된 `backend/sql/runtime_roles.sql`을 사용한다.
세부 변경은 [리팩토링 결과](refactoring-20260916.md)를 참고한다.
