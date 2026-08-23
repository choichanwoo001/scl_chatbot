# SCL 챗봇 프로토타입 Design QA

## 비교 대상

- 원본 데스크톱: `../../../evidence/source/scl-home-desktop-top.png`
- 구현 데스크톱: `../../../evidence/implementation/scl-prototype-desktop-v2.png`
- 데스크톱 비교본: `../../../evidence/comparison/desktop-source-left-implementation-right.png`

## 정규화 조건

- CSS viewport: 1440 × 900, device scale factor 1
- 원본과 구현 캡처: 각각 1425 × 891px
- source-left / implementation-right 순서로 동일 크기에서 비교
- 구현 범위: 데스크톱 고정형 우측 채팅 패널

## 최종 확인

- Pretendard Regular/SemiBold/Bold 로컬 자산과 원본의 타이포그래피 위계를 유지한다.
- 80px 데스크톱 헤더, 메인 비주얼, 공문 섹션의 레이아웃 리듬을 유지한다.
- SCL 딥블루와 조회 패널 계열의 CSS 토큰을 사용한다.
- 로고, 비주얼, 뉴스, Spotlight, 건강 콘텐츠 이미지는 로컬 자산을 사용한다.
- 입력 label, 버튼 accessible name, `aria-live`, reduced-motion, 키보드 전송과 focus outline을 제공한다.
- 챗봇 열기/닫기, 대표 질문, 검사 카드, 세션 후속 질문과 입력 방어 흐름을 확인했다.
- 렌더링과 주요 상호작용에서 console warning/error가 없다.

## 남은 확인

- 실제 운영 DB 연결 후 긴 검사명과 다중 출처 문구의 wrapping을 추가 점검한다.
- 실제 VoiceOver/NVDA 읽기 순서와 브라우저 200% 확대를 별도 검증한다.

final result: passed
