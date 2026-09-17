"""Evaluate on an isolated SQLite copy; live Gemini calls require --live.

Run from the repository root:
  .venv/Scripts/python scripts/evaluate_query_interpretation.py --baseline PATH
  .venv/Scripts/python scripts/evaluate_query_interpretation.py --live
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import dotenv_values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("tmp/semantic-query-eval"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    source = root / "data/scl_catalog.db"
    dest = out / "catalog.db"
    if source.resolve() == dest.resolve():
        raise ValueError("Evaluation database must be separate from the source")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)
    local = dict(dotenv_values(root / ".env")) if args.live else {}
    os.environ.update(
        SCL_SKIP_LOCAL_ENV="true",
        APP_ENV="test",
        DATABASE_URL="sqlite:///" + dest.as_posix(),
        SEED_DEMO_ON_EMPTY="false",
        VECTOR_SEARCH_ENABLED="false",
        LLM_PROVIDER="gemini",
        GEMINI_API_KEY=local.get("GEMINI_API_KEY") or "",
        GEMINI_MODEL=local.get("GEMINI_MODEL") or "gemini-3.1-flash-lite",
    )
    sys.path.insert(0, str(root / "backend"))
    from app.catalog import catalog
    from app.config import settings
    from app.orchestrator import ChatOrchestrator
    from app.query_interpretation import (
        Identifier,
        QueryInterpretation,
        code_candidates,
        execute_query,
        validate_query,
    )

    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        rows = catalog.search_rows()
        catalog.search_rows = lambda: rows
        results = []
        for case in baseline["results"]:
            if not (case["group"].startswith("billing_") or case["group"] == "unknown_billing"):
                continue
            query = QueryInterpretation(
                intent="test",
                identifiers=[
                    Identifier(kind="billing", value=code) for code in code_candidates(case["query"])
                ],
            )
            error = validate_query(query, case["query"], [])
            items, _ = execute_query(catalog, query, [])
            actual = sorted(item.variant_key for item in items)
            results.append(
                {
                    "group": case["group"],
                    "query": case["query"],
                    "pass": not error and actual == sorted(case["expected"]),
                    "expected": sorted(case["expected"]),
                    "actual": actual,
                }
            )
        summary = {
            group: {
                "n": sum(r["group"] == group for r in results),
                "passed": sum(r["group"] == group and r["pass"] for r in results),
            }
            for group in sorted({r["group"] for r in results})
        }
        report = {
            "scope": "Exact result-set equality; interpretation supplied, no live Gemini",
            "summary": summary,
            "results": results,
        }
        (out / "offline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False), flush=True)

    if args.live:
        runtime = ChatOrchestrator(settings)
        if runtime.gateway is None:
            raise RuntimeError("Gemini key is not configured")
        original = runtime.gateway.interpret
        last_query = []

        def interpret(*a, **kw):
            result = original(*a, **kw)
            last_query[:] = [result[0]]
            return result

        runtime.gateway.interpret = interpret
        cases = [
            ("D517205KZ", False, {"test:16290:510"}),
            ("D517205KZ 보험코드", False, {"test:16290:510"}),
            ("D517205KZ 보험코드를 조회해줘", False, {"test:16290:510"}),
            ("D517205KZ 해당하는 보험코드를 가진 검사 조회좀", False, {"test:16290:510"}),
            ("아니 그럼 급여코드 D517205KZ 이걸로 조회", False, {"test:16290:510"}),
            ("급여코드 D517205KZ로 검사 찾아줘", False, {"test:16290:510"}),
            ("그 검사 소요일이랑 보험코드 알려줘", True, {"test:16290:510"}),
            ("보험 코드가 D517205KZ인 항목 좀 보여줄래?", False, {"test:16290:510"}),
            ("급여코드 Z990000ZZ로 검사 찾아줘", False, set()),
            ("D517205KZ 검사 중 소변으로 하는 것만", False, set()),
            ("Z990000ZZ 말고 D517205KZ로 찾아줘", False, {"test:16290:510"}),
        ]
        results = []

        async def run():
            session = None
            for message, followup, expected in cases:
                try:
                    response = await runtime.respond(
                        message, session if followup else None, require_live=True
                    )
                    session = response.session_id
                    refs = {citation.ref for citation in response.reply.citations}
                    passed = (
                        refs == expected
                        and response.domain == "test"
                        and (bool(expected) or "찾지 못했습니다" in response.reply.text)
                    )
                    if followup:
                        passed = (
                            passed and "5일" in response.reply.text and "D517205KZ" in response.reply.text
                        )
                    result = {
                        "query": message,
                        "pass": passed,
                        "interpretation": last_query[0].model_dump(),
                        "response": response.model_dump(mode="json"),
                    }
                    results.append(result)
                    print(
                        json.dumps(
                            {
                                "query": message,
                                "pass": passed,
                                "refs": sorted(refs),
                                "text": response.reply.text[:140],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                except Exception as error:
                    cause = error.__cause__ or error
                    results.append(
                        {
                            "query": message,
                            "pass": False,
                            "error": type(cause).__name__,
                            "status": getattr(getattr(cause, "response", None), "status_code", None),
                        }
                    )
                    print(json.dumps(results[-1], ensure_ascii=False), flush=True)
                    break
                finally:
                    (out / "live.json").write_text(
                        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
                    )

        try:
            asyncio.run(run())
        finally:
            runtime.gateway.client.close()


if __name__ == "__main__":
    main()
