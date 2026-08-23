from __future__ import annotations

import argparse
import json

from app.config import settings
from app.vector_index import OpenAIVectorIndexService
from openai import OpenAI


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SCL 공개 데이터를 OpenAI Vector Store와 동기화합니다.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--delete-stale", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--types", nargs="*", choices=["document", "attachment", "faq"])
    parser.add_argument("--create-store", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY가 필요합니다.")
    client = OpenAI(api_key=settings.openai_api_key, timeout=60, max_retries=2)
    vector_store_id = settings.openai_vector_store_id
    if args.create_store:
        store = client.vector_stores.create(
            name="SCL public knowledge",
            description="SCL 공개 문서, 추출 첨부, 게시 FAQ의 의미 검색 인덱스",
        )
        vector_store_id = store.id
        print(f"created vector store: {vector_store_id}")
        print(f"OPENAI_VECTOR_STORE_ID={vector_store_id}")
    if not vector_store_id:
        raise SystemExit("OPENAI_VECTOR_STORE_ID가 필요합니다. 최초 1회는 --create-store를 사용하세요.")
    service = OpenAIVectorIndexService(
        settings,
        client=client,
        vector_store_id=vector_store_id,
    )
    report = service.sync(
        types=set(args.types) if args.types else None,
        limit=args.limit,
        dry_run=args.dry_run,
        delete_stale=args.delete_stale,
        force=args.force,
        batch_size=args.batch_size,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
