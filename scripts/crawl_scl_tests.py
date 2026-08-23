from __future__ import annotations

import argparse
import json

from app.config import settings
from app.scl_crawler import SCLTestCrawler


def main() -> None:
    parser = argparse.ArgumentParser(description="SCL 공개 검사항목을 RDB에 동기화합니다.")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="개발 검증용 페이지 제한. 생략하면 전체를 수집합니다.",
    )
    args = parser.parse_args()
    summary = SCLTestCrawler(settings).crawl_and_ingest(max_pages=args.max_pages)
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
