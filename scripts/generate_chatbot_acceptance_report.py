from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "artifacts" / "chatbot-test-results-20260921.json"
REPORT = ROOT / "docs" / "chatbot-question-test-results-20260921.md"

JUDGMENTS = {
    1: ("통과", "없음."),
    2: ("통과", "없음."),
    3: ("통과", "조건은 정확하지만 후보가 118건이라 답변과 링크가 길다. 대표 예시와 추가 필터 안내를 더 앞세울 여지가 있다."),
    4: ("통과", "없음. 용기 유형 2개로 묶고 대표 링크만 제공한다."),
    5: ("통과", "없음. 앞서 조회한 10135를 기억했다."),
    6: ("통과", "없음. 두 후보가 모두 1일인 공동 최단임을 명확히 표시했다."),
    7: ("통과", "두 번째 후보 ALT(10130)는 정확히 선택했지만 주의사항과 임상적 의의 원문이 지나치게 길다."),
    8: ("통과", "없음. 토요일 조건과 검사요일을 함께 표시했다."),
    9: ("통과", "없음."),
    10: ("통과", "없음. 보험코드를 SCL 검사코드와 구분했다."),
    11: ("통과", "없음. 두 후보에 서로 다른 상세 링크가 연결됐다."),
    12: ("통과", "없음."),
    13: ("통과", "없음. 명시한 검사코드 10135를 우선했다."),
    14: ("통과", "혼합형 코드를 정확히 찾아 α1-Antitrypsin이라고 답했다. 다만 '무슨 검사'에 대한 용도 설명은 공개 자료에 없어 제한적이다."),
    15: ("통과", "없음. 유사 검사로 억지 연결하지 않았다."),
    16: ("통과", "없음."),
    17: ("통과", "없음. 붙여쓰기와 '검채' 오타를 처리했다."),
    18: ("통과", "없음."),
    19: ("통과", "없음. 7건 모두 1일로 공동 최단임을 표시했다."),
    20: ("통과", "소변 조건은 정확히 적용했지만 후보가 286건이라 답변이 길다."),
    21: ("통과", "요청한 네 필드를 모두 제공했다. 같은 검체명이 각 줄의 머리와 필드에 한 번씩 반복되는 표현은 다듬을 수 있다."),
    22: ("통과", "없음. 검사요일과 소요일을 분리했다."),
    23: ("실패", "'후보별로 검사코드와 검체를 같이 보여줘'를 지원 불가 조건으로 잘못 분류했다."),
    24: ("통과", "없음."),
    25: ("통과", "핵심 차이와 공통점을 구분했다. 다만 '공개 데이터에 없음'은 'SCL 공개 페이지에 별도 주의사항 미기재'로 바꾸는 편이 정확하다."),
    26: ("통과", "없음."),
    27: ("통과", "없음."),
    28: ("통과", "최신 2026-08-18 공문을 첫 결과로 제공했다. 보조 링크에는 2026년 다른 공문보다 2024년 자료가 먼저 포함되는 개선 여지가 있다."),
    29: ("통과", "없음."),
    30: ("통과", "없음. 검사 안내서와 관련 공문을 함께 제공했다."),
    31: ("통과", "없음. 대구남부 주소와 전화번호를 직접 제공했다."),
    32: ("통과", "없음."),
    33: ("통과", "없음. 개인 결과 조회와 공개 검사정보를 구분했다."),
    34: ("통과", "없음. 임의 판단을 제한하고 의료진 또는 상담 채널을 안내했다."),
    37: ("통과", "없음. ALT 후보 2건을 표시했다."),
}

UI_RESULTS = [
    {
        "number": 38,
        "question": "표시된 첫 번째 검사 항목 클릭",
        "answer": "새 탭에서 (특검)ALT 상세 페이지가 열림: itemcode=10135&sampcode=100",
        "judgment": "통과",
        "problem": "없음. 메시지로 재전송되지 않았다.",
    },
    {
        "number": 39,
        "question": "표시된 두 번째 검사 항목 클릭",
        "answer": "새 탭에서 ALT 상세 페이지가 열림: itemcode=10130&sampcode=100",
        "judgment": "통과",
        "problem": "없음. 첫 번째 검사와 다른 정확한 상세 페이지가 열렸다.",
    },
    {
        "number": 40,
        "question": "상세 링크를 연 뒤 원래 채팅으로 돌아오기",
        "answer": "원래 로컬 챗봇 탭으로 돌아왔고 질문과 ALT 후보 2건이 그대로 유지됨.",
        "judgment": "통과",
        "problem": "없음.",
    },
]


def quoted(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def main() -> None:
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    statuses = [JUDGMENTS[item["number"]][0] for item in results]
    statuses.extend(item["judgment"] for item in UI_RESULTS)
    lines = [
        "# SCL 챗봇 질문 테스트 결과",
        "",
        "- 테스트 일자: 2026-09-21",
        "- API 대상: `http://127.0.0.1:5173/api/chat`",
        "- 링크 동작: Chrome에서 실제 클릭 검증",
        f"- 결과: 통과 {statuses.count('통과')}건 / 실패 {statuses.count('실패')}건",
        "- 원본 API 기록: `artifacts/chatbot-test-results-20260921.json`",
        "",
        "## 질문별 기록",
        "",
    ]
    for item in results:
        judgment, problem = JUDGMENTS[item["number"]]
        lines.extend(
            [
                f"### {item['number']}",
                "",
                f"- 질문: {item['question']}",
                "- 답변:",
                "",
                quoted(item["answer"] or f"오류: {item['error']}"),
                "",
                f"- 판정: **{judgment}**",
                f"- 문제: {problem}",
            ]
        )
        citations = item.get("citations") or []
        if citations:
            links = ", ".join(
                f"[{citation['title']}]({citation.get('url')})" for citation in citations
            )
            lines.append(f"- 링크: {links}")
        lines.append("")
    for item in UI_RESULTS:
        lines.extend(
            [
                f"### {item['number']}",
                "",
                f"- 질문/동작: {item['question']}",
                f"- 답변/결과: {item['answer']}",
                f"- 판정: **{item['judgment']}**",
                f"- 문제: {item['problem']}",
                "",
            ]
        )
    lines.extend(
        [
            "## 확인된 개선 항목",
            "",
            "1. 23번의 '후보별 검사코드와 검체' 표현을 지원 불가 조건으로 분류하지 않도록 수정 필요.",
            "2. 7번 상세 응답의 임상적 의의·주의사항 원문을 요약하거나 별도 영역으로 분리.",
            "3. 25번의 '공개 데이터에 없음'을 'SCL 공개 페이지에 별도 주의사항 미기재'로 변경.",
            "4. 3번과 20번처럼 후보가 많은 검색은 대표 결과와 추가 필터 안내를 우선 표시.",
            "5. 28번 최신 공문의 보조 링크도 날짜순으로 안정적으로 정렬.",
            "",
        ]
    )
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
