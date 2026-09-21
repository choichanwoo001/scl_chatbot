from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE_URL = "http://127.0.0.1:5173/api/chat"
OUTPUT = Path(__file__).resolve().parents[1] / "artifacts" / "chatbot-test-results-20260921.json"

CASES = [
    (1, "간 검사하려면 어떤 검사를 보면 돼?", []),
    (2, "피 뽑아서 하는 갑상선 검사 알려줘", []),
    (3, "소변으로 하는 검사 중에 당일 나오는 거 있어?", []),
    (4, "HPV 검사할 때 어떤 통에 담아야 해?", []),
    (5, "이 검사 결과 나오려면 며칠 정도 걸려?", ["10135 검사 찾아줘"]),
    (
        6,
        "그중에서 제일 빨리 나오는 걸로 보여줘",
        ["D185000HZ 보험코드에 해당하는 검사 알려줘"],
    ),
    (
        7,
        "아까 나온 것 중 두 번째 검사 자세히 보여줘",
        ["D185000HZ 보험코드에 해당하는 검사 알려줘"],
    ),
    (8, "간수치 검사 중 토요일에도 하는 검사가 뭐야?", []),
    (9, "10135 검사 찾아줘", []),
    (10, "D185000HZ 보험코드에 해당하는 검사 알려줘", []),
    (11, "D185000HZ로 검색했는데 두 개가 나오면 각각 어디서 확인해?", []),
    (12, "ALT Serum 검사 찾아줘", []),
    (13, "검사코드 10135인 ALT 항목 열어줘", []),
    (14, "R0329가 무슨 검사야?", []),
    (15, "X999999ZZ라는 코드가 있어?", []),
    (16, "에이엘티 검사 알려줘", []),
    (17, "ALT검사 소요기간하고 검채 알려줘", []),
    (18, "간 기능 보는 피검사 뭐였지?", []),
    (19, "결과 빨리 나오는 갑상선 관련 검사", []),
    (20, "혈액 말고 소변으로 할 수 있는 검사는?", []),
    (21, "HPV 검사의 검체, 용기, 검사방법, 소요일을 한 번에 알려줘", []),
    (22, "ALT 검사는 무슨 요일에 하고 결과는 언제 나와?", []),
    (23, "갑상선 검사 후보별로 검사코드와 검체를 같이 보여줘", []),
    (24, "Serum으로 검사하고 하루 안에 나오는 ALT 관련 항목 찾아줘", []),
    (25, "10130이랑 10135는 뭐가 달라?", []),
    (26, "검체용기 종류를 확인할 수 있는 페이지로 보내줘", []),
    (27, "검사 의뢰 방법이 어디에 나와 있어?", []),
    (28, "최근 신규검사 안내 공문 찾아줘", []),
    (29, "2026년 8월 연휴 검사일정 안내문 찾아줘", []),
    (30, "HPV 관련 공문이나 검사 안내서가 있어?", []),
    (31, "대구 SCL 지점 주소와 전화번호 알려줘", []),
    (32, "제주에서 가장 가까운 SCL 센터 연락처 알려줘", []),
    (33, "검사결과는 어디에서 확인해?", []),
    (34, "검사결과 수치가 정상인지 해석해줘", []),
    (37, "D185000HZ 보험코드 검사 찾아줘", []),
]


def ask(message: str, session_id: str | None = None) -> dict:
    payload = {"message": message, "require_live": False}
    if session_id:
        payload["session_id"] = session_id
    request = urllib.request.Request(
        BASE_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    results = []
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    for index, (number, question, preludes) in enumerate(CASES, start=1):
        started = time.perf_counter()
        session_id = None
        try:
            setup = []
            for prelude in preludes:
                prelude_response = ask(prelude, session_id)
                session_id = prelude_response["session_id"]
                setup.append({"question": prelude, "answer": prelude_response["reply"]["text"]})
            response = ask(question, session_id)
            reply = response["reply"]
            result = {
                "number": number,
                "question": question,
                "setup": setup,
                "answer": reply["text"],
                "kind": reply["kind"],
                "citations": reply.get("citations", []),
                "answerability": reply.get("answerability"),
                "domain": response.get("domain"),
                "safety_action": response.get("safety_action"),
                "needs_handoff": response.get("needs_handoff"),
                "duration_seconds": round(time.perf_counter() - started, 2),
                "error": None,
            }
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            result = {
                "number": number,
                "question": question,
                "setup": [],
                "answer": None,
                "citations": [],
                "duration_seconds": round(time.perf_counter() - started, 2),
                "error": str(error),
            }
        results.append(result)
        OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        first_line = (result.get("answer") or result.get("error") or "").splitlines()[0]
        print(f"[{index}/{len(CASES)}] Q{number}: {first_line}", flush=True)
    print(f"RESULT_FILE={OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
