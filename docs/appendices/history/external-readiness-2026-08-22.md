# 외부 연동·문서 처리 준비상태 점검

기준일: 2026-08-22

## 결론

코드와 로컬 데이터로 해결할 수 있는 항목은 완료했다. 개인 검사결과의 실제 라이브 호출만
기관이 승인한 Gateway URL·클라이언트 자격증명·샌드박스 계정이 없어 실행할 수 없다.
공개 홈페이지의 로그인 폼을 운영 API처럼 자동화하지 않고, 명시적인 HTTPS Gateway 계약으로
분리했다.

## 개인 검사결과

- `HTTPResultProvider` 구현: 인증, cursor 목록, 상세, logout
- HTTPS 강제, redirect 차단, 사설 CA, mTLS, client ID/secret 지원
- 401/403 만료, 404, 429, 5xx/transport 오류 매핑
- Pydantic 응답 계약 검증과 최대 20페이지 안전 제한
- 제공자 만료시간과 로컬 최대 15분 중 짧은 세션 사용
- 사용자 ID·비밀번호·본인확인 값은 OpenAI·DB·브라우저 저장소에 저장하지 않음
- 외부 호출 엔드포인트는 FastAPI thread pool에서 실행하고 토큰 저장소는 동시성 잠금 사용
- 현재 계약 파일: `../technical/result-provider-openapi.yaml`

공개 페이지에서 확인 가능한 흐름은 홈페이지 `/front/actionLogin.do`, PC 결과조회
`/front/WebResultIndex.do`, `r-esmart.scllab.co.kr` 세션 전달이다. 인증 후 목록·상세 응답과
공식 지원 정책은 공개 API 문서가 없으므로 추측해 구현하지 않았다. 기관 내부 시스템이 OpenAPI
Gateway 계약을 구현하거나 공식 명세를 제공해야 한다.

## 첨부파일·OCR

| 상태 | 건수 | 의미 |
|---|---:|---|
| `extracted` | 1,862 | RDB 본문·청크 검색 가능 |
| `ocr_no_text` | 70 | OCR은 성공했지만 유효 문자가 없는 이미지 |
| `source_unavailable` | 6 | 공개 원본 서버가 파일을 더 이상 제공하지 않음 |
| 미처리/실패/OCR 대기/지원 제외/용량 초과 | 0 | 로컬에서 조치할 항목 없음 |

- 총 검색 본문: 15,588,359자
- Tesseract 5.x, `kor+eng`, PyMuPDF PDF 렌더링
- 이미지 EXIF 회전, grayscale/autocontrast, 800만 픽셀 안전 축소
- ZIP 내부 PDF·Office·HWP·텍스트와 이미지 OCR
- 구형 `.doc`는 LibreOffice headless 변환
- 파일 signature 검사, SCL HTTPS host 제한, ZIP member·압축해제 크기 제한
- 최대 첨부 100MB, PDF 최대 250쪽, 페이지별 OCR 180초
- Docker 이미지에도 Tesseract 한국어 모델과 LibreOffice 포함

원본 소실 6건은 attachment `445`, `893`, `1267`, `1269`, `1843`, `1844`다. 이는 기관
보관본을 받아야 복구할 수 있다.

## 함께 발견해 수정한 항목

- 프런트 세션 종료 `DELETE`의 CORS preflight 허용 누락
- 외부 결과 HTTP 호출의 event-loop blocking 가능성
- 결과 토큰 메모리 저장소의 동시 접근 가능성
- 잘못된 Provider mode의 조용한 fallback
- 특수문자 포함 직접 첨부 URL 인코딩
- PDF로 표기됐지만 실제 JPG/PNG/WebP인 원본 탐지
- Docker build context의 `.env`, Fernet 키, 1GB 이상 원본 첨부 제외

## 후속 Vector Store 범위 확장

후속 범위 결정에 따라 공개문서·추출 첨부·게시 FAQ의 OpenAI Vector Store 증분 색인,
서버 제어 의미 검색, RDB 검색과의 하이브리드 순위, 장애 폴백, 상태·감사·평가 도구를 추가했다.
외부 저장소 생성과 3,640건의 최초 업로드는 비용과 외부 상태 변경이 생기므로 운영 승인 후
현재 `../technical/vector-store-runbook.md` 절차로 실행한다. 기본 런타임은 `VECTOR_SEARCH_ENABLED=false`다.

관리자 화면, 대화 영구 저장, 스트리밍, rate limit, 운영 로그 수집 시스템, 자동 스케줄러,
전문가 검수 자동화는 여전히 구현 대상에서 제외한다. 상담은 암호화된 내부 RDB 접수,
피드백은 RDB와 CLI FAQ 검토 흐름을 유지한다.

## 검증 명령

```powershell
$env:PYTHONPATH='backend'
.venv\Scripts\python.exe scripts\validate_public_data.py
.venv\Scripts\python.exe scripts\audit_external_integrations.py --strict
.venv\Scripts\python.exe -m pytest backend\tests -q
docker compose build backend
```

최종 결과: 데이터 검증 `ok=true`, 외부 로컬 준비상태 `ok=true`, OpenAI 라이브 평가 9/9,
백엔드 84개 테스트 통과, 프런트 12개와 Sites 4개 테스트 통과, Docker 이미지 build 및
container health 통과.
