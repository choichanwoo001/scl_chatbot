from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from app.config import settings
from app.vector_search import OpenAIVectorSearchProvider

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "data" / "evals" / "vector_search_questions.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenAI Vector Store 의미 검색 품질을 평가합니다.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--strict", action="store_true", help="미설정 또는 기준 미달이면 실패")
    parser.add_argument("--min-hit-rate", type=float, default=0.85)
    parser.add_argument("--min-mrr", type=float, default=0.70)
    return parser.parse_args()


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile_value))
    return ordered[index]


def main() -> None:
    args = parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not settings.vector_search_configured:
        report = {
            "ok": False,
            "skipped": True,
            "reason": "VECTOR_SEARCH_ENABLED, OPENAI_API_KEY, OPENAI_VECTOR_STORE_ID가 모두 필요합니다.",
            "cases": len(cases),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.strict:
            sys.exit(1)
        return

    provider = OpenAIVectorSearchProvider(settings)
    results: list[dict[str, object]] = []
    reciprocal_rank_total = 0.0
    latencies_ms: list[float] = []
    for case in cases:
        started = time.perf_counter()
        hits = provider.search(case["query"], set(case.get("types", [])) or None, case["top_k"])
        elapsed_ms = (time.perf_counter() - started) * 1000
        latencies_ms.append(elapsed_ms)
        expected_refs = set(case["expected_refs"])
        matched_rank = next(
            (rank for rank, hit in enumerate(hits, 1) if hit.ref in expected_refs),
            None,
        )
        if matched_rank:
            reciprocal_rank_total += 1 / matched_rank
        results.append(
            {
                "query": case["query"],
                "passed": matched_rank is not None,
                "rank": matched_rank,
                "expected_refs": sorted(expected_refs),
                "top_refs": [hit.ref for hit in hits],
                "latency_ms": round(elapsed_ms, 1),
            }
        )

    passes = sum(bool(item["passed"]) for item in results)
    hit_rate = passes / len(cases) if cases else 1.0
    mrr = reciprocal_rank_total / len(cases) if cases else 1.0
    report = {
        "ok": hit_rate >= args.min_hit_rate and mrr >= args.min_mrr,
        "skipped": False,
        "cases": len(cases),
        "passes": passes,
        "hit_rate_at_k": round(hit_rate, 4),
        "mean_reciprocal_rank": round(mrr, 4),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies_ms), 1) if latencies_ms else 0.0,
            "p50": round(percentile(latencies_ms, 0.5), 1),
            "p95": round(percentile(latencies_ms, 0.95), 1),
        },
        "thresholds": {"min_hit_rate": args.min_hit_rate, "min_mrr": args.min_mrr},
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and not report["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
