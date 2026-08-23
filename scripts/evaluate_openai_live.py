from __future__ import annotations

import json
import sys
from dataclasses import replace
from typing import Any

from app.catalog import catalog
from app.config import settings
from app.openai_gateway import ModelPlan, OpenAIGateway
from app.public_search import public_search

CASES: list[dict[str, Any]] = [
    {"name": "test_typo", "query": "HPV 검사항목 용기랑 소요일 알려조", "domains": {"test"}},
    {"name": "document", "query": "2026년 8월 연휴 검사일정 공문 찾아줘", "domains": {"document"}},
    {
        "name": "navigation",
        "query": "홈페이지에서 검사결과 확인은 어디로 가?",
        "domains": {"result"},
        "action": "navigate",
    },
    {
        "name": "personal_result",
        "query": "내 개인 검사결과 수치가 비정상인데 해석해줘",
        "domains": {"result"},
        "flags": {"needs_handoff": True, "medical_review_required": True},
    },
    {
        "name": "account",
        "query": "비밀번호를 잊었어 재설정하고 싶어",
        "domains": {"account"},
        "flags": {"requires_authentication": True},
    },
    {
        "name": "support",
        "query": "검체 배송 문제로 상담원 연결해줘",
        "domains": {"support"},
        "flags": {"needs_handoff": True},
    },
    {"name": "unsupported", "query": "오늘 서울 날씨 알려줘", "domains": {"unsupported"}},
    {
        "name": "multi_intent",
        "query": "HPV 검사 용기랑 최신 검사일정 공문도 같이 알려줘",
        "domains": {"test", "document"},
        "flags": {"has_multiple_intents": True},
    },
]


def _references_resolve(plan: ModelPlan) -> bool:
    if plan.matched_test_code and not catalog.get(plan.matched_test_code, plan.matched_test_variant_key):
        return False
    resolved_variants = [catalog.get(None, key) for key in plan.candidate_test_variant_keys]
    if any(item is None for item in resolved_variants):
        return False
    for code in plan.candidate_test_codes:
        exact = [item for item in catalog.search(code, limit=20) if item.code.casefold() == code.casefold()]
        if not exact:
            return False
    for ref in plan.matched_document_ids:
        normalized = ref if ":" in ref else f"document:{ref}"
        if not public_search.get_ref(normalized):
            return False
    if plan.matched_content_id and not public_search.get_ref(plan.matched_content_id):
        return False
    return not (plan.target_route and not public_search.find_route(plan.target_route))


def _evaluate(plan: ModelPlan, case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if plan.domain not in case["domains"]:
        errors.append(f"domain={plan.domain}")
    if case.get("action") and plan.requested_action != case["action"]:
        errors.append(f"requested_action={plan.requested_action}")
    for field, expected in case.get("flags", {}).items():
        if getattr(plan, field) is not expected:
            errors.append(f"{field}={getattr(plan, field)}")
    if not _references_resolve(plan):
        errors.append("unresolvable_reference")
    return errors


def _run() -> None:
    if not settings.openai_api_key:
        print(json.dumps({"ok": False, "error": "OPENAI_API_KEY is not configured"}, ensure_ascii=False))
        sys.exit(2)

    # This evaluation deliberately excludes vector/file search even if an environment later sets it.
    gateway = OpenAIGateway(replace(settings, openai_vector_store_id=None))
    results: list[dict[str, Any]] = []
    history: list[dict[str, str]] = []
    first_test_plan: ModelPlan | None = None

    for case in CASES:
        try:
            flagged = gateway.moderate(case["query"])
            if flagged:
                results.append({"name": case["name"], "passed": False, "errors": ["moderation_flagged"]})
                continue
            plan, response_id = gateway.plan(case["query"], [])
        except Exception as error:
            results.append(
                {
                    "name": case["name"],
                    "passed": False,
                    "errors": [type(error).__name__],
                    "status_code": getattr(error, "status_code", None),
                }
            )
            continue
        errors = _evaluate(plan, case)
        if case["name"] == "test_typo":
            first_test_plan = plan
            history = [
                {"role": "user", "content": case["query"]},
                {"role": "assistant", "content": plan.answer},
            ]
        results.append(
            {
                "name": case["name"],
                "passed": not errors,
                "domain": plan.domain,
                "sub_intent": plan.sub_intent,
                "action": plan.requested_action,
                "confidence": plan.confidence,
                "response_id_present": bool(response_id),
                "errors": errors,
            }
        )

    followup_errors: list[str] = []
    if first_test_plan is None:
        followup_errors.append("missing_first_turn")
    else:
        try:
            followup, response_id = gateway.plan("그 검사는 며칠 걸려?", history)
            if followup.domain != "test":
                followup_errors.append(f"domain={followup.domain}")
            if not _references_resolve(followup):
                followup_errors.append("unresolvable_reference")
            results.append(
                {
                    "name": "followup",
                    "passed": not followup_errors,
                    "domain": followup.domain,
                    "sub_intent": followup.sub_intent,
                    "action": followup.requested_action,
                    "confidence": followup.confidence,
                    "response_id_present": bool(response_id),
                    "errors": followup_errors,
                }
            )
        except Exception as error:
            results.append(
                {
                    "name": "followup",
                    "passed": False,
                    "errors": [type(error).__name__],
                    "status_code": getattr(error, "status_code", None),
                }
            )

    summary = {
        "ok": all(item["passed"] for item in results),
        "model": settings.openai_chat_model,
        "vector_store_used": False,
        "cases": len(results),
        "passes": sum(bool(item["passed"]) for item in results),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["ok"]:
        sys.exit(1)


def main() -> None:
    try:
        _run()
    except Exception as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(error).__name__,
                    "status_code": getattr(error, "status_code", None),
                    "vector_store_used": False,
                    "message": "OpenAI live evaluation could not start or complete.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
