from __future__ import annotations

import argparse
import json

from app.config import settings
from app.database import SessionLocal
from app.models import VectorIndexItem
from app.vector_index import RDBVectorDocumentSource, vector_index_status
from sqlalchemy import select


def main() -> None:
    parser = argparse.ArgumentParser(description="로컬 Vector Store 색인 매핑을 감사합니다.")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    eligible = RDBVectorDocumentSource().list_documents()
    eligible_refs = {document.local_ref for document in eligible}
    status = vector_index_status()
    completed_refs: set[str] = set()
    active_mapping_refs: set[str] = set()
    if settings.openai_vector_store_id:
        with SessionLocal() as session:
            mappings = list(
                session.scalars(
                    select(VectorIndexItem).where(
                        VectorIndexItem.vector_store_id == settings.openai_vector_store_id
                    )
                )
            )
        completed_refs = {
            item.local_ref for item in mappings if item.index_status == "completed"
        }
        active_mapping_refs = {
            item.local_ref for item in mappings if item.index_status != "deleted"
        }
    covered_refs = eligible_refs & completed_refs
    coverage = len(covered_refs) / len(eligible_refs) if eligible_refs else 1.0
    result = {
        **status,
        "eligible": len(eligible),
        "covered": len(covered_refs),
        "unmapped": len(eligible_refs - completed_refs),
        "stale_active": len(active_mapping_refs - eligible_refs),
        "coverage": coverage,
        "ok": bool(
            status["configured"]
            and coverage >= 0.99
            and not status["counts"].get("failed")
            and not status.get("items_with_errors")
            and not (active_mapping_refs - eligible_refs)
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.strict and not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
