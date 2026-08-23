from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings
from app.database import SessionLocal
from app.models import AttachmentContent
from app.ocr_engine import TesseractOCREngine
from app.vector_index import vector_index_status
from sqlalchemy import func


def office_converter() -> str | None:
    if settings.office_converter_cmd and Path(settings.office_converter_cmd).is_file():
        return settings.office_converter_cmd
    located = shutil.which("soffice")
    if located:
        return located
    for path in (
        Path("C:/Program Files/LibreOffice/program/soffice.com"),
        Path("C:/Program Files/LibreOffice/program/soffice.exe"),
        Path("C:/Program Files (x86)/LibreOffice/program/soffice.com"),
        Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
    ):
        if path.is_file():
            return str(path)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="외부 연동 및 로컬 문서 엔진 준비상태 점검")
    parser.add_argument("--strict", action="store_true", help="코드로 해결 가능한 미처리 상태가 있으면 실패")
    args = parser.parse_args()

    with SessionLocal() as session:
        attachment_statuses = {
            status: count
            for status, count in session.query(AttachmentContent.extraction_status, func.count()).group_by(
                AttachmentContent.extraction_status
            )
        }

    result_url = urlparse(settings.result_api_base_url or "")
    result_configured = (
        settings.result_provider_mode == "http"
        and bool(result_url.hostname)
        and (result_url.scheme == "https" or settings.result_api_allow_http)
    )
    ocr = TesseractOCREngine(settings).status()
    converter = office_converter()
    vector_status = vector_index_status()
    actionable = []
    for status in ("ocr_required", "unsupported", "failed"):
        if attachment_statuses.get(status, 0):
            actionable.append(f"attachments:{status}={attachment_statuses[status]}")
    if settings.ocr_enabled and not ocr.get("available"):
        actionable.append("ocr:engine_or_languages_unavailable")
    if not converter:
        actionable.append("documents:libreoffice_unavailable")

    external_requirements = []
    if not result_configured:
        external_requirements.append("기관 승인 결과 Gateway URL·클라이언트 자격증명·샌드박스 계정")
    if attachment_statuses.get("source_unavailable", 0):
        external_requirements.append("원본 서버에서 소실된 첨부파일의 기관 보관본")
    if attachment_statuses.get("too_large", 0):
        external_requirements.append("대용량 첨부 처리 상한·저장용량에 대한 운영 결정")

    report = {
        "ok": not actionable,
        "openai": {
            "configured": bool(settings.openai_api_key),
            "model": settings.openai_chat_model,
            "vector_search_enabled": settings.vector_search_enabled,
            "vector_search_configured": settings.vector_search_configured,
            "vector_search_shadow_mode": settings.vector_search_shadow_mode,
            "vector_index_counts": vector_status.get("counts", {}),
            "vector_index_usage_bytes": vector_status.get("usage_bytes", 0),
            "vector_index_items_with_errors": vector_status.get("items_with_errors", 0),
            "vector_index_last_synced_at": vector_status.get("last_indexed_at"),
        },
        "personal_results": {
            "adapter_implemented": True,
            "mode": settings.result_provider_mode,
            "configured": result_configured,
            "https": result_url.scheme == "https",
            "mtls_configured": bool(settings.result_api_client_cert),
            "contract": "docs/appendices/technical/result-provider-openapi.yaml",
        },
        "ocr": ocr,
        "legacy_office": {"available": bool(converter), "executable": converter},
        "attachments": attachment_statuses,
        "handoff": {"storage": "encrypted_rdb", "external_ticket_delivery": False},
        "feedback": {"storage": "rdb", "faq_review": "cli"},
        "actionable_local_items": actionable,
        "external_requirements": external_requirements,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and actionable:
        sys.exit(1)


if __name__ == "__main__":
    main()
