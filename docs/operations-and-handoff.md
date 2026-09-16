# 운영·인수인계 가이드

기준일: 2026-08-28

## 1. 운영 프로필

| 프로필 | LLM | Vector | 저장소 | 결과 Provider | 목적 |
|---|---|---|---|---|---|
| 자동 테스트 | 없음 | 꺼짐 | 인메모리·임시 DB | mock·unconfigured | 비용 없는 결정적 검증 |
| 로컬 데모 | Gemini 또는 OpenAI 선택 | 선택 | SQLite·메모리 세션 | unconfigured | UI·RDB 시연 |
| 공개 Sites | Gemini 우선, 검색 fallback | Gemini 로컬 Index | D1 세션·상담·일일 호출량 | unconfigured | 제한된 공개 시연 |
| 운영 후보 | 승인 provider | 품질 평가 후 활성 | PostgreSQL·공유 세션 | 승인된 HTTPS Gateway | 기관 승인 후 |

`mock` 결과 Provider는 자동 테스트 전용이며 공개 시연·운영에 사용하지 않는다.

## 2. 필수 설정

로컬에서는 루트 `.env.example`을 `.env`로 복사해 사용한다. 백엔드, Vite와 Docker Compose가
같은 파일을 읽는다. Sites 배포값은 같은 변수명을 Sites 런타임 설정에 등록한다.

| 설정 | 필수 시점 | 설명 |
|---|---|---|
| `LLM_PROVIDER` | FastAPI provider 선택 | 현재 `gemini` |
| `GEMINI_API_KEY` | Gemini 실시간 채팅·임베딩 | 서버 전용 |
| `GEMINI_MODEL` | Gemini 실시간 채팅 | 기본 `gemini-3.1-flash-lite` |
| `GEMINI_DAILY_REQUEST_LIMIT` | Sites 배포 | D1로 관리하는 일일 호출 상한, 기본 `20` |
| `DATABASE_URL` | 외부 DB 사용 | 기본 SQLite |
| `MIGRATION_DATABASE_URL` | Alembic 실행 | migration 계정 전용 |
| `INGEST_DATABASE_URL` | 수집 작업 | `scl_ingest` 계정 전용 |
| `SUPABASE_URL` | Sites Worker의 실시간 공개 카탈로그 조회 | 미설정 시 내장 스냅샷 |
| `SUPABASE_PUBLISHABLE_KEY` | Supabase 공개 RPC 호출 | 미설정 시 내장 스냅샷 |
| `FIELD_ENCRYPTION_KEY` | 운영 상담 접수 | 고정 Fernet 키 |
| `HANDOFF_ENCRYPTION_KEY` | Sites 상담 접수 | 고정 AES 키 |
| `GEMINI_VECTOR_INDEX_PATH` | Gemini Vector | 기본 `data/gemini_vector_index.json` |
| `VECTOR_SEARCH_ENABLED` | Vector 전환 | `.env.example`은 `true`, 미설정 코드 기본값은 `false` |
| `SEED_DEMO_ON_EMPTY` | 개발·단위 테스트만 | 운영 기본 `false` |

## 3. 배포와 기동

### Docker Compose

담당자는 프로젝트 루트에서 최신 `main`과 Git LFS 공개 데이터 스냅샷을 받은 뒤 실행한다.

```powershell
git checkout main
git pull
git lfs pull
docker compose up --build
```

백그라운드 실행이 필요하면 마지막 명령에 `-d`를 추가하고 `docker compose ps`로 상태를 확인한다.

### 로컬 백엔드

```powershell
$env:PYTHONPATH="backend"
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 로컬 프런트

```powershell
Set-Location frontend
npm ci
npm run dev -- --host 127.0.0.1
```

### Sites Worker

`frontend/.openai/hosting.json`의 D1 binding `DB`와 서버 Secret을 설정한 뒤 호스팅 빌드를
배포한다. Worker 시작 시 세션·상담·Gemini 일일 호출량 테이블을 생성한다. 공개 UI는
`require_live=false`를 보내므로 Gemini 실패 시 검증된 배포 스냅샷 검색으로 복귀한다.

## 4. 배포 후 확인

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/catalog/status
Invoke-RestMethod http://127.0.0.1:8000/api/public-data/status
```

확인 항목:

- `status=ok`
- 실시간 환경은 `live_chat_available=true`
- 의도한 Vector enabled/configured/shadow 값
- Vector 완료·실패·오류 보유 건수
- 검사·문서·첨부 건수가 직전 승인 스냅샷과 일치

## 5. 공개 데이터 갱신

```powershell
$env:PYTHONPATH="backend"
.venv\Scripts\python scripts\crawl_scl_tests.py
.venv\Scripts\python scripts\sync_scl_public_data.py all
.venv\Scripts\python scripts\sync_taxonomy_links.py
.venv\Scripts\python scripts\extract_attachments.py --workers 6
.venv\Scripts\python scripts\extract_attachments.py --retry-status ocr_required,failed --workers 6
.venv\Scripts\python scripts\validate_public_data.py
.venv\Scripts\python scripts\evaluate_public_search.py
```

부분 수집은 기존 항목을 비활성화하지 않는다. 전체 동기화와 무결성·검색 평가가 모두 성공한
뒤에만 새 데이터 스냅샷을 승인한다.

## 6. Vector 전환

### Gemini 로컬 인덱스

```powershell
$env:PYTHONPATH="backend"
.venv\Scripts\python scripts\build_gemini_vector_index.py
.venv\Scripts\python scripts\evaluate_public_search.py
```

생성된 `data/gemini_vector_index.json`의 모델·차원·건수와 공개 ref를 확인한 뒤 배포
스냅샷을 갱신한다. 인덱스 또는 Gemini API가 실패하면 키워드 검색을 유지한다.

### OpenAI Vector Store 선택 경로

외부 저장소 생성과 업로드는 비용·외부 상태 변경이므로 승인된 운영자가 실행한다.

```powershell
$env:PYTHONPATH="backend"
.venv\Scripts\python scripts\audit_vector_store.py
.venv\Scripts\python scripts\ingest_openai_files.py --create-store --limit 20
.venv\Scripts\python scripts\evaluate_vector_search.py
.venv\Scripts\python scripts\ingest_openai_files.py --dry-run
.venv\Scripts\python scripts\ingest_openai_files.py --batch-size 50
.venv\Scripts\python scripts\audit_vector_store.py --strict
```

1. `VECTOR_SEARCH_ENABLED=true`, `VECTOR_SEARCH_SHADOW_MODE=true`로 배포한다.
2. 호출 오류·지연과 기존 검색 순위를 비교한다.
3. 의미 검색 평가 기준을 통과하면 shadow를 끈다.
4. 이상 시 `VECTOR_SEARCH_ENABLED=false`로 롤백하고 재시작한다.

상세 내용은 [Vector Store 기술 부록](appendices/technical/vector-store-runbook.md)을 따른다.

## 7. FAQ 운영

```powershell
$env:PYTHONPATH="backend"
.venv\Scripts\python scripts\manage_faq.py list --status draft
.venv\Scripts\python scripts\manage_faq.py publish <candidate_id>
```

사용자 피드백은 바로 Vector Store나 공개 답변에 반영하지 않는다. 담당자 검토와 게시 후 다음
Vector 증분 동기화에서 반영한다.

## 8. 백업과 복구

SQLite 운영 시 쓰기 트래픽을 중단하거나 일관된 DB snapshot 기능을 사용해
`data/scl_catalog.db`와 운영 `FIELD_ENCRYPTION_KEY`를 서로 다른 보안 저장소에 백업한다.
암호화 키가 없으면 기존 상담 암호문을 복호화할 수 없다.

복구 절차:

1. 백엔드 쓰기 중단
2. 손상 DB 별도 보존
3. 승인된 DB snapshot 복원
4. 동일한 암호화 키 주입
5. `validate_public_data.py` 실행
6. 상태 API와 대표 검색 확인
7. 백엔드 재개

PostgreSQL 전환 시 기관 표준 백업·PITR 정책으로 대체한다.

## 9. 장애 대응표

| 증상 | 즉시 조치 | 후속 확인 |
|---|---|---|
| Gemini/OpenAI 5xx·timeout | 공개 UI는 검색 fallback, `require_live=true`는 503 | 모델 상태·호출량·네트워크·키 |
| Vector 오류 증가 | Vector 비활성화 | 로컬 Index 또는 Store 상태·매핑·커버리지 |
| 검색 품질 저하 | 직전 데이터 snapshot 사용 | 동기화 revision·평가셋 |
| OCR 실패 증가 | 재시도 중단·원본 보존 | Tesseract 언어·용량·페이지 |
| 결과 Gateway 오류 | 결과 기능만 중단 | HTTPS·mTLS·계약 응답 |
| 암호화 키 불일치 | 상담 데이터 쓰기 중단 | Secret 버전·복구 승인 |

## 10. 정기 점검 권장

- 배포마다: lint, 전체 테스트, 데이터 검증, 검색 평가
- 데이터 갱신마다: 건수 변화, 비활성화, 첨부 실패, Vector dry-run
- 주기적: Secret·인증서 만료, 백업 복구 훈련, 의존성 취약점
- 모델 변경마다: 관리자 시나리오와 Structured Output 평가 재실행

현재 중앙 모니터링·알림·스케줄러는 없으므로 운영 도입 전 별도 구현이 필요하다.
