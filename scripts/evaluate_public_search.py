from __future__ import annotations

import json
import sys
from pathlib import Path

from app.config import settings
from app.orchestrator import ChatOrchestrator, SessionState
from app.public_search import public_search

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "data" / "evals" / "public_search_questions.json"


def main() -> None:
    cases = json.loads(DEFAULT_CASES.read_text(encoding="utf-8"))
    orchestrator = ChatOrchestrator(settings)
    results = []
    reciprocal_rank_total = 0.0
    chat_passes = chat_total = 0
    for case in cases:
        hits = public_search.search(case["query"], case.get("types"), max(case["top_k"], 10))
        matched_rank = None
        for index, hit in enumerate(hits[: case["top_k"]], 1):
            if hit.entity_type == case["expected_type"] and any(
                keyword.casefold() in hit.title.casefold() for keyword in case["title_any"]
            ):
                matched_rank = index
                break
        if matched_rank:
            reciprocal_rank_total += 1 / matched_rank
        chat_ok = None
        if case.get("chat_status"):
            chat_total += 1
            reply, domain = orchestrator._demo_reply(case["query"], SessionState())
            chat_ok = (
                reply.data_status == case["chat_status"]
                and domain == case["chat_domain"]
                and bool(reply.citations)
            )
            chat_passes += int(chat_ok)
        results.append(
            {
                "query": case["query"],
                "passed": matched_rank is not None,
                "rank": matched_rank,
                "top_ref": hits[0].ref if hits else None,
                "top_title": hits[0].title if hits else None,
                "chat_passed": chat_ok,
            }
        )
    retrieval_passes = sum(item["passed"] for item in results)
    summary = {
        "ok": retrieval_passes == len(cases) and chat_passes == chat_total,
        "cases": len(cases),
        "retrieval_passes": retrieval_passes,
        "hit_rate_at_k": round(retrieval_passes / len(cases), 4),
        "mean_reciprocal_rank": round(reciprocal_rank_total / len(cases), 4),
        "chat_cases": chat_total,
        "chat_passes": chat_passes,
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
