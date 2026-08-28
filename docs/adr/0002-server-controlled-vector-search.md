# ADR-0002: 서버 제어 Vector 검색

- 상태: Accepted
- 결정일: 2026-08-23
- 구현 확장: 2026-08-28

## 배경

모델이 직접 `file_search`를 사용하면 실제 검색 결과, 점수, 로컬 참조 매핑과 실패 동작을 서버가
일관되게 통제하기 어렵다.

## 결정

서버가 provider별 Vector 검색을 직접 실행하고 키워드 결과와 결합한다. Gemini 경로는 공개
문서의 로컬 임베딩 인덱스를 검색하고, OpenAI 경로는 Vector Store Search API를 사용한다.
어느 경로든 결과를 로컬 ref·공개 상태와 다시 대조하며 모델의 직접 `file_search`는 사용하지 않는다.

## 결과

- 검색 점수·유형 필터·fallback을 서버가 통제한다.
- Vector 장애 시 RDB 검색으로 즉시 복귀한다.
- shadow mode에서 기존 순위를 유지한 채 선택 provider 결과를 평가할 수 있다.
- 로컬 인덱스 또는 원격 Store의 매핑·커버리지·stale 항목을 운영해야 한다.
