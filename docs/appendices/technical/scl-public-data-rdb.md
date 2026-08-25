# SCL 공개 데이터 RDB 동기화

검사항목 카탈로그 외에 홈페이지 안내 챗봇이 사용하는 공개 데이터를 SCL 홈페이지에서 수집한다.
모든 레코드는 출처 URL과 마지막 확인 시각을 보존하며, 문서는 첨부와 변경 revision을 별도로 관리한다.

## 1~8단계 데이터셋

| 단계 | 데이터셋 | 주요 테이블 | 현재 레코드 |
|---|---|---|---:|
| 1 | 검체용기 | `containers`, `container_aliases`, `container_test_mentions` | 53 / 50 / 89 |
| 2 | 공문·검사 변경 안내 | `public_documents`, `document_attachments` | 883 |
| 3 | 24시간뇨 요보존제 | `preservative_guides` | 53 |
| 4 | 의뢰서·학술 리플릿 | `public_documents`, `document_attachments` | 151 |
| 5 | 검사실 담당 분야·질환군 | `taxonomy_terms`, `taxonomy_relations`, `test_taxonomy_links` | 13 + 7 / 연결 377 |
| 6 | 전국 네트워크·지역검사센터 | `service_locations` | 66 |
| 7 | 메뉴·서비스 경로 | `site_routes` | 56 |
| 8 | 뉴스·건강·사회공헌 콘텐츠 | `public_documents`, `document_attachments` | 1,024 |

공문·자료·콘텐츠는 `document_type`으로 구분한다. 같은 파일 ID가 여러 카드에서 재사용될 수 있어
카드형 콘텐츠의 원본 키는 게시물/파일 식별자와 제목을 함께 해시한다. 사이트맵은 같은 URL을 여러
메뉴에서 반복하므로 URL을 canonical key로 사용한다.

자주 하는 질문 게시판은 `faqs` 데이터셋으로 전체 페이지를 수집하고, 질문을 제목으로, 공식 답변을
본문으로 저장한다. `document_type=official_faq` 결과는 검사 관련 자연어 질문에서 일반 문서보다
우선 검색한다.

## 동기화와 검증

```powershell
$env:PYTHONPATH="backend"
.venv\Scripts\python scripts\sync_scl_public_data.py all
.venv\Scripts\python scripts\validate_public_data.py
```

운영에서는 첫 실행 후 같은 명령을 다시 실행한다. 두 번째 실행의 각 결과는 `inserted=0`,
`updated=0`, `unchanged=records`여야 한다. 검증 스크립트는 예상 건수, 업무 키 중복, 필수값,
첨부/용기 언급 orphan, 최신 동기화의 멱등성, SQLite 외래키 위반을 검사한다.

`dataset_sync_runs`는 성공과 실패 실행을 모두 남긴다. 엔터티 내용이 달라지면
`entity_revisions`에 스냅샷과 변경 필드가 추가되고, 수집 도중 오류가 발생하면 해당 데이터 저장
트랜잭션은 롤백된다.

## 통합 검색·챗봇 연결

`GET /api/search`는 `test`, `document`, `container`, `preservative`, `location`, `route`,
`taxonomy`를 함께 검색한다. `types=location,route`처럼 유형을 제한할 수 있으며 결과의 `ref`는
LLM Structured Output이 선택하는 안정적인 식별자다. 오케스트레이터는 선택된 `ref`를 DB에서 다시
조회한 후에만 답변과 인용에 사용한다.

질환–검사 관계는 `curated_rule_v1` 규칙으로 생성하며 자동 연결은 `verified=false`다. 운영자가
검토한 뒤에만 `verified=true`로 바꾼다.

```powershell
$env:PYTHONPATH="backend"
.venv\Scripts\python scripts\sync_taxonomy_links.py
.venv\Scripts\python scripts\evaluate_public_search.py
```

평가셋은 `data/evals/public_search_questions.json`에 있으며 검색 Hit@K, MRR, 데모 챗봇의 업무 영역,
데이터 상태, 출처 존재 여부를 검사한다.
