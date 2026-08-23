from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "data" / "evals" / "admin_chatbot_scenarios.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="관리자 시연용 챗봇 API 시나리오 평가")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--id", action="append", dest="scenario_ids")
    return parser.parse_args()


def evaluate(body: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    reply = body.get("reply") or {}
    citations = reply.get("citations") or []

    checks = {
        "mode": body.get("mode"),
        "safety_action": body.get("safety_action"),
        "requires_authentication": body.get("requires_authentication"),
        "needs_handoff": body.get("needs_handoff"),
        "medical_review_required": body.get("medical_review_required"),
    }
    for field, actual in checks.items():
        if field in expected and actual != expected[field]:
            errors.append(f"{field}={actual!r}")

    if "domain_in" in expected and body.get("domain") not in expected["domain_in"]:
        errors.append(f"domain={body.get('domain')!r}")
    if "kind_in" in expected and reply.get("kind") not in expected["kind_in"]:
        errors.append(f"kind={reply.get('kind')!r}")
    if "data_status_in" in expected and reply.get("data_status") not in expected["data_status_in"]:
        errors.append(f"data_status={reply.get('data_status')!r}")
    if len(citations) < expected.get("citation_min", 0):
        errors.append(f"citations={len(citations)}")
    if "citation_max" in expected and len(citations) > expected["citation_max"]:
        errors.append(f"citations={len(citations)}")
    for marker in expected.get("displayed_contains", []):
        if marker not in str(body.get("displayed_input") or ""):
            errors.append(f"displayed_input_missing={marker!r}")
    for marker in expected.get("answer_contains", []):
        if marker.casefold() not in str(reply.get("text") or "").casefold():
            errors.append(f"answer_missing={marker!r}")
    if reply.get("kind") == "test" and not reply.get("test"):
        errors.append("missing_test_payload")
    return errors


def main() -> None:
    args = parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.scenario_ids:
        selected = set(args.scenario_ids)
        cases = [case for case in cases if case["id"] in selected]
        missing = selected - {case["id"] for case in cases}
        if missing:
            raise SystemExit(f"unknown scenario id: {', '.join(sorted(missing))}")
    results: list[dict[str, Any]] = []

    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        health_response = client.get("/health")
        health_response.raise_for_status()
        health = health_response.json()

        for case in cases:
            session_id = f"admin-eval-{uuid.uuid4().hex}"
            turn_results = []
            for index, turn in enumerate(case["turns"], 1):
                try:
                    response = client.post(
                        "/api/chat",
                        json={
                            "message": turn["message"],
                            "session_id": session_id,
                            "require_live": True,
                        },
                    )
                    response.raise_for_status()
                    body = response.json()
                    errors = evaluate(body, turn["expect"])
                    turn_results.append(
                        {
                            "turn": index,
                            "message": turn["message"],
                            "passed": not errors,
                            "errors": errors,
                            "domain": body.get("domain"),
                            "sub_intent": body.get("sub_intent"),
                            "kind": (body.get("reply") or {}).get("kind"),
                            "data_status": (body.get("reply") or {}).get("data_status"),
                            "citations": len((body.get("reply") or {}).get("citations") or []),
                            "mode": body.get("mode"),
                        }
                    )
                except Exception as error:
                    turn_results.append(
                        {
                            "turn": index,
                            "message": turn["message"],
                            "passed": False,
                            "errors": [type(error).__name__],
                        }
                    )
                    break
            passed = all(item["passed"] for item in turn_results)
            results.append(
                {
                    "id": case["id"],
                    "group": case["group"],
                    "passed": passed,
                    "turns": turn_results,
                }
            )
            print(
                f"[{len(results):02d}/{len(cases):02d}] {'PASS' if passed else 'FAIL'} {case['id']}",
                flush=True,
            )

    summary = {
        "ok": all(item["passed"] for item in results),
        "health": health,
        "scenarios": len(results),
        "turns": sum(len(item["turns"]) for item in results),
        "passes": sum(bool(item["passed"]) for item in results),
        "results": results,
    }
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if not summary["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
