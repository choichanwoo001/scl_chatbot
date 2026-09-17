from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .config import Settings, settings
from .database import SessionLocal, init_database
from .models import AttachmentContent, DocumentAttachment, FAQCandidate, PublicDocument
from .vector_search import VECTOR_ENTITY_TYPES


@dataclass(frozen=True)
class VectorDocument:
    local_ref: str
    entity_type: str
    entity_id: str
    file_name: str
    content: str
    content_hash: str
    source_updated_at: datetime | None
    attributes: dict[str, str | float | bool]


class RDBVectorDocumentSource:
    def __init__(self) -> None:
        init_database()

    def list_documents(
        self,
        types: set[str] | None = None,
        limit: int | None = None,
    ) -> list[VectorDocument]:
        selected = (set(types) if types else set(VECTOR_ENTITY_TYPES)) & VECTOR_ENTITY_TYPES
        documents: list[VectorDocument] = []
        with SessionLocal() as session:
            if "document" in selected:
                rows = session.scalars(select(PublicDocument).where(PublicDocument.status == "active"))
                for item in rows:
                    body = (item.body_text or item.summary or "").strip()
                    if not body:
                        continue
                    local_ref = f"document:{item.id}"
                    documents.append(
                        self._document(
                            local_ref=local_ref,
                            entity_type="document",
                            entity_id=str(item.id),
                            file_name=f"document-{item.id}.md",
                            content=self._markdown(
                                title=item.title,
                                local_ref=local_ref,
                                entity_type="document",
                                source_url=item.source_url,
                                published_at=item.published_at,
                                body=body,
                            ),
                            source_updated_at=item.updated_at,
                            attributes={
                                "entity_type": "document",
                                "local_ref": local_ref,
                                "entity_id": str(item.id),
                                "document_type": item.document_type,
                            },
                        )
                    )
            if "attachment" in selected:
                rows = session.execute(
                    select(AttachmentContent, DocumentAttachment, PublicDocument)
                    .join(DocumentAttachment, DocumentAttachment.id == AttachmentContent.attachment_id)
                    .join(PublicDocument, PublicDocument.id == DocumentAttachment.document_id)
                    .where(
                        AttachmentContent.extraction_status == "extracted",
                        DocumentAttachment.status == "active",
                        PublicDocument.status == "active",
                    )
                )
                for content_row, attachment, document in rows:
                    body = (content_row.extracted_text or "").strip()
                    if not body:
                        continue
                    local_ref = f"attachment:{attachment.id}"
                    documents.append(
                        self._document(
                            local_ref=local_ref,
                            entity_type="attachment",
                            entity_id=str(attachment.id),
                            file_name=f"attachment-{attachment.id}.md",
                            content=self._markdown(
                                title=attachment.file_name,
                                local_ref=local_ref,
                                entity_type="attachment",
                                source_url=document.source_url,
                                published_at=document.published_at,
                                body=body,
                                parent_title=document.title,
                            ),
                            source_updated_at=content_row.extracted_at,
                            attributes={
                                "entity_type": "attachment",
                                "local_ref": local_ref,
                                "entity_id": str(attachment.id),
                                "document_id": str(document.id),
                                "file_type": attachment.file_type or "unknown",
                            },
                        )
                    )
            if "faq" in selected:
                rows = session.scalars(select(FAQCandidate).where(FAQCandidate.status == "published"))
                for item in rows:
                    local_ref = f"faq:{item.id}"
                    documents.append(
                        self._document(
                            local_ref=local_ref,
                            entity_type="faq",
                            entity_id=str(item.id),
                            file_name=f"faq-{item.id}.md",
                            content=self._markdown(
                                title=item.canonical_question,
                                local_ref=local_ref,
                                entity_type="faq",
                                source_url=None,
                                published_at=item.published_at,
                                body=item.canonical_answer,
                            ),
                            source_updated_at=item.updated_at,
                            attributes={
                                "entity_type": "faq",
                                "local_ref": local_ref,
                                "entity_id": str(item.id),
                                "domain": item.domain or "unknown",
                                "sub_intent": item.sub_intent or "unknown",
                            },
                        )
                    )
        documents.sort(key=lambda item: item.local_ref)
        return documents[:limit] if limit else documents

    @staticmethod
    def _document(
        *,
        local_ref: str,
        entity_type: str,
        entity_id: str,
        file_name: str,
        content: str,
        source_updated_at: datetime | None,
        attributes: dict[str, str | float | bool],
    ) -> VectorDocument:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return VectorDocument(
            local_ref=local_ref,
            entity_type=entity_type,
            entity_id=entity_id,
            file_name=file_name,
            content=content,
            content_hash=digest,
            source_updated_at=source_updated_at,
            attributes={**attributes, "content_hash": digest},
        )

    @staticmethod
    def _markdown(
        *,
        title: str,
        local_ref: str,
        entity_type: str,
        source_url: str | None,
        published_at: datetime | None,
        body: str,
        parent_title: str | None = None,
    ) -> str:
        metadata = [f"# {title.strip()}", "", f"- local_ref: {local_ref}", f"- entity_type: {entity_type}"]
        if parent_title:
            metadata.append(f"- parent_document: {parent_title.strip()}")
        if published_at:
            metadata.append(f"- published_at: {published_at.date().isoformat()}")
        if source_url:
            metadata.append(f"- source_url: {source_url}")
        return "\n".join([*metadata, "", "## 본문", "", body.strip(), ""])


def vector_index_status(app_settings: Settings = settings) -> dict[str, Any]:
    path = Path(app_settings.gemini_vector_index_path)
    if not path.is_file():
        return {
            "configured": False,
            "vector_store_id": None,
            "counts": {},
            "completed_by_type": {},
            "usage_bytes": 0,
            "items_with_errors": 0,
            "last_indexed_at": None,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = list(payload.get("items") or [])
    by_type: dict[str, int] = {}
    for item in items:
        entity_type = str(item.get("entity_type") or "unknown")
        by_type[entity_type] = by_type.get(entity_type, 0) + 1
    return {
        "configured": app_settings.vector_search_configured,
        "vector_store_id": f"local:{path.name}",
        "counts": {"completed": len(items)},
        "completed_by_type": by_type,
        "usage_bytes": path.stat().st_size,
        "items_with_errors": 0,
        "last_indexed_at": payload.get("created_at"),
    }
