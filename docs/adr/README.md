# Architecture Decision Records

ADR은 현재 코드의 중요한 선택과 트레이드오프를 기록한다. 상태가 `Accepted`인 문서는 현재 설계의
기준이며, 변경 시 기존 문서를 지우지 않고 새 ADR로 대체 관계를 기록한다.

| ADR | 결정 | 상태 |
|---|---|---|
| [0001](0001-rdb-source-of-truth.md) | RDB를 최종 신뢰 원본으로 사용 | Accepted |
| [0002](0002-server-controlled-vector-search.md) | 모델 직접 File Search 대신 서버 제어 검색 | Accepted |
| [0003](0003-structured-output-routing.md) | Structured Outputs 기반 의도 라우팅 | Accepted |
| [0004](0004-personal-results-isolation.md) | 개인 결과 Provider를 LLM 경로에서 격리 | Accepted |
