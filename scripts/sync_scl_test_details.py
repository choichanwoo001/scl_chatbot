from __future__ import annotations

import argparse
import json

from app.config import settings
from app.scl_test_details import SCLTestDetailSync


def main() -> None:
    parser = argparse.ArgumentParser(description="SCL 공개 검사 상세 페이지를 RDB에 동기화합니다.")
    parser.add_argument("--limit", type=int, default=None, help="개발 검증용 검사 변형 수 제한")
    parser.add_argument("--workers", type=int, default=4, help="동시 요청 수(1~8)")
    parser.add_argument("--only-missing", action="store_true", help="상세 정보가 없는 항목만 수집")
    args = parser.parse_args()

    def progress(completed: int, total: int, failed: int) -> None:
        print(f"progress={completed}/{total} failed={failed}", flush=True)

    result = SCLTestDetailSync(settings).sync(
        limit=args.limit,
        workers=args.workers,
        only_missing=args.only_missing,
        progress=progress,
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
