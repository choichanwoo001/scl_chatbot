from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from app.database import SessionLocal
from app.models import FAQCandidate
from sqlalchemy import select


def main() -> None:
    parser = argparse.ArgumentParser(description="FAQ 후보 조회·게시 도구")
    subparsers = parser.add_subparsers(dest="command", required=True)
    listing = subparsers.add_parser("list")
    listing.add_argument("--status", default="draft", choices=["draft", "published", "rejected"])
    publish = subparsers.add_parser("publish")
    publish.add_argument("candidate_id", type=int)
    reject = subparsers.add_parser("reject")
    reject.add_argument("candidate_id", type=int)
    args = parser.parse_args()

    with SessionLocal.begin() as session:
        if args.command == "list":
            items = list(
                session.scalars(
                    select(FAQCandidate)
                    .where(FAQCandidate.status == args.status)
                    .order_by(FAQCandidate.occurrence_count.desc(), FAQCandidate.id)
                )
            )
            print(
                json.dumps(
                    [
                        {
                            "id": item.id,
                            "question": item.canonical_question,
                            "occurrences": item.occurrence_count,
                            "positive": item.positive_count,
                            "negative": item.negative_count,
                            "status": item.status,
                        }
                        for item in items
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        item = session.get(FAQCandidate, args.candidate_id)
        if not item:
            raise SystemExit(f"FAQ candidate {args.candidate_id} not found")
        item.status = "published" if args.command == "publish" else "rejected"
        item.published_at = datetime.now(UTC) if item.status == "published" else None
        print(json.dumps({"id": item.id, "status": item.status}, ensure_ascii=False))


if __name__ == "__main__":
    main()
