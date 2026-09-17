from __future__ import annotations

import argparse
import json

from app.vector_index import RDBVectorDocumentSource, vector_index_status


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini 로컬 벡터 인덱스 커버리지를 감사합니다.")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    eligible = RDBVectorDocumentSource().list_documents()
    status = vector_index_status()
    completed = int(status.get("counts", {}).get("completed", 0))
    coverage = min(completed / len(eligible), 1.0) if eligible else 1.0
    result = {
        **status,
        "eligible": len(eligible),
        "completed": completed,
        "coverage": coverage,
        "ok": bool(status["configured"] and coverage >= 0.99),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.strict and not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
