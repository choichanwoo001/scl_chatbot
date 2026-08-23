# ADR-0002: 서버 제어 Vector 검색

- 상태: Accepted
- 결정일: 2026-08-23

## 배경

모델이 직접 `file_search`를 사용하면 실제 검색 결과, 점수, 로컬 참조 매핑과 실패 동작을 서버가
일관되게 통제하기 어렵다.

## 결정

백엔드가 OpenAI Vector Store Search API를 직접 호출하고 키워드 결과와 결합한다. 결과는 완료된
`vector_index_items` 매핑과 공개 RDB 원문으로 재검증한다.

## 결과

- 검색 점수·유형 필터·fallback을 서버가 통제한다.
- Vector 장애 시 RDB 검색으로 즉시 복귀한다.
- shadow mode에서 기존 순위를 유지한 채 원격 결과를 평가할 수 있다.
- 인덱스 매핑·커버리지·stale 파일을 운영해야 한다.
