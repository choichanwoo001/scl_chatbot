from __future__ import annotations

import argparse
import json

from app.attachment_ingestion import AttachmentIngestionService
from app.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="SCL 공개 첨부파일 본문 추출")
    parser.add_argument("--attachment-id", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--retry-status",
        help="쉼표로 구분한 재처리 상태(예: ocr_required,too_large,unsupported)",
    )
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    service = AttachmentIngestionService(settings)
    if args.attachment_id:
        item = service.process(args.attachment_id, force=args.retry_failed)
        result = {
            "attachment_id": item.attachment_id,
            "status": item.extraction_status,
            "extractor": item.extractor,
            "char_count": item.char_count,
            "error": item.error_message,
        }
    else:
        retry_statuses = {value.strip() for value in (args.retry_status or "").split(",") if value.strip()}
        result = service.process_pending(
            args.limit,
            retry_failed=args.retry_failed,
            retry_statuses=retry_statuses,
            workers=args.workers,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
