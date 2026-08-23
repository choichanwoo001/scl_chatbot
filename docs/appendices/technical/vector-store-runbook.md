# OpenAI Vector Store 운영 절차

## 데이터 범위

Vector Store에는 로컬 RDB에서 공개 상태가 확인된 아래 데이터만 들어갑니다.

- 본문 또는 요약이 있는 활성 공개문서
- 활성 공개문서에 연결되고 텍스트 추출이 완료된 첨부파일
- 검토 후 `published` 상태가 된 FAQ

검사항목, 개인 검사결과, 상담 접수 내용, 채팅 기록, 초안 FAQ는 업로드하지 않습니다. 검색 결과는
`vector_index_items`의 완료 매핑과 로컬 RDB 원문을 다시 확인한 뒤에만 챗봇 문맥으로 사용합니다.

## 최초 전환

아래 명령은 OpenAI에 저장소를 만들거나 파일을 업로드하므로 운영 승인 후 실행합니다.

```powershell
$env:PYTHONPATH="backend"

# 1. 외부 변경 없이 대상 건수와 예정 작업을 확인
.venv\Scripts\python scripts\audit_vector_store.py

# 2. 최초 저장소 생성 및 소량 동기화
.venv\Scripts\python scripts\ingest_openai_files.py --create-store --limit 20

# 출력된 OPENAI_VECTOR_STORE_ID를 .env 또는 비밀 저장소에 등록한 뒤 재시작

# 3. 소량 색인 상태 및 의미 검색 품질 확인
.venv\Scripts\python scripts\audit_vector_store.py
.venv\Scripts\python scripts\evaluate_vector_search.py

# 4. 전체 증분 동기화. 먼저 dry-run 결과를 확인
.venv\Scripts\python scripts\ingest_openai_files.py --dry-run
.venv\Scripts\python scripts\ingest_openai_files.py --batch-size 50
.venv\Scripts\python scripts\audit_vector_store.py --strict
```

`--limit`을 사용한 소량 동기화 뒤의 커버리지는 당연히 100%가 아니므로 strict 감사는 전체 동기화 후에만
사용합니다.

## 안전한 활성화 순서

1. `VECTOR_SEARCH_ENABLED=true`, `VECTOR_SEARCH_SHADOW_MODE=true`로 배포합니다.
2. `/health`와 `/api/public-data/status`에서 설정, 완료 건수, 오류 수를 확인합니다.
3. 기존 공개 검색 평가와 벡터 검색 평가를 모두 통과시킵니다.
4. 오류율과 지연시간을 확인한 뒤 `VECTOR_SEARCH_SHADOW_MODE=false`로 전환합니다.

Vector Store가 미설정이거나 검색 호출이 실패하면 기존 RDB 키워드 검색 결과를 그대로 사용합니다.
저장소 ID만 있고 `VECTOR_SEARCH_ENABLED=false`이면 외부 벡터 검색을 호출하지 않습니다.

## 증분 갱신과 롤백

```powershell
$env:PYTHONPATH="backend"

# 변경된 공개 데이터만 갱신
.venv\Scripts\python scripts\ingest_openai_files.py

# 로컬 공개 데이터에서 사라진 원격 파일까지 정리
.venv\Scripts\python scripts\ingest_openai_files.py --delete-stale

# 즉시 롤백: .env에서 아래 값만 false로 바꾸고 백엔드 재시작
VECTOR_SEARCH_ENABLED=false
```

`--delete-stale`은 원격 파일을 삭제하므로 전체 공개 데이터 동기화가 정상 완료된 시점에만 사용합니다.
