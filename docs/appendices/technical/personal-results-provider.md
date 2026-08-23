# 개인 검사결과 Provider 연동 계약

개인 검사결과 인증값과 결과 데이터는 OpenAI에 전달하지 않는다. 브라우저는 일반 `/api/chat`과 분리된
`/api/results/*` 엔드포인트로만 인증하고, 백엔드는 Provider가 돌려준 임시 토큰만 메모리에 15분간 유지한다.
아이디, 비밀번호, 본인확인 값은 DB·로그·브라우저 `sessionStorage`에 저장하지 않는다.

## 구현된 외부 Gateway 계약

`RESULT_PROVIDER_MODE=http`이면 `backend/app/result_provider.py`의 `HTTPResultProvider`가
같은 디렉터리의 `result-provider-openapi.yaml` 계약을 호출한다. 인증, cursor pagination, 상세조회,
logout, 401/403 만료 처리, 429/5xx 장애 처리, 응답 스키마 검증, HTTPS 강제, 사설 CA와
mTLS 인증서를 구현했다. 제공자 토큰의 만료시간과 로컬 최대 15분 중 짧은 값을 사용한다.

공개 홈페이지 조사 결과 PC 결과조회는 `/front/WebResultIndex.do`에서
`https://r-esmart.scllab.co.kr/web_result/session_proc.jsp`로 세션을 넘기고, 홈페이지 로그인은
`/front/actionLogin.do` 폼을 사용한다. 이는 공개·안정형 JSON API가 아니며 승인되지 않은 HTML
자동화는 인증정보 노출과 화면 변경 위험이 있어 운영 Provider로 사용하지 않는다. 내부 시스템 또는
승인된 중계 Gateway가 아래 OpenAPI 계약을 구현해야 한다.

## 기관에서 받아야 하는 정보

1. 인증 API base URL, HTTP method, request/response 예시
2. 아이디·비밀번호 외 본인확인 필드의 이름과 검증 규칙
3. 성공 토큰 형식, 만료시간, refresh/logout 방식
4. 신청 검사 목록 API와 pagination 규칙
5. 결과 상세 API와 결과 상태 코드
6. 검사명, 신청일, 보고일, 결과 필드, 참고치의 실제 JSON 매핑
7. 샌드박스 URL과 테스트 계정
8. 네트워크 allowlist, TLS 인증서 또는 mTLS 요구사항

## 설정

`.env.example`의 `RESULT_API_*` 값을 기관 Gateway에 맞게 설정한다. 개발 중 로컬 HTTP는
명시적으로 `RESULT_API_ALLOW_HTTP=true`인 경우에만 허용한다. 운영에서는 HTTPS를 강제하고,
필요하면 `RESULT_API_CA_BUNDLE`, `RESULT_API_CLIENT_CERT`, `RESULT_API_CLIENT_KEY`로 사설 CA와
mTLS를 설정한다.

실제 Provider는 인증정보나 결과 원문을 application logger에 남기지 않아야 한다. 의료적 해석은 결과 카드에
자동 생성하지 않고 기존 상담 흐름으로 분리한다.
