from __future__ import annotations

import hashlib
import io
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from openai import OpenAI
from sqlalchemy import select

from .config import Settings, settings
from .database import SessionLocal, init_database
from .models import (
    AttachmentContent,
    DocumentAttachment,
    FAQCandidate,
    PublicDocument,
    VectorIndexItem,
    utcnow,
)
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


@dataclass
class VectorSyncReport:
    vector_store_id: str
    eligible: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: int = 0
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
                rows = session.scalars(
                    select(PublicDocument).where(PublicDocument.status == "active")
                )
                for item in rows:
                    body = (item.body_text or item.summary or "").strip()
                    if not body:
                        continue
                    local_ref = f"document:{item.id}"
                    content = self._markdown(
                        title=item.title,
                        local_ref=local_ref,
                        entity_type="document",
                        source_url=item.source_url,
                        published_at=item.published_at,
                        body=body,
                    )
                    documents.append(
                        self._document(
                            local_ref=local_ref,
                            entity_type="document",
                            entity_id=str(item.id),
                            file_name=f"document-{item.id}.md",
                            content=content,
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
                    content = self._markdown(
                        title=attachment.file_name,
                        local_ref=local_ref,
                        entity_type="attachment",
                        source_url=document.source_url,
                        published_at=document.published_at,
                        body=body,
                        parent_title=document.title,
                    )
                    documents.append(
                        self._document(
                            local_ref=local_ref,
                            entity_type="attachment",
                            entity_id=str(attachment.id),
                            file_name=f"attachment-{attachment.id}.md",
                            content=content,
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
                rows = session.scalars(
                    select(FAQCandidate).where(FAQCandidate.status == "published")
                )
                for item in rows:
                    local_ref = f"faq:{item.id}"
                    content = self._markdown(
                        title=item.canonical_question,
                        local_ref=local_ref,
                        entity_type="faq",
                        source_url=None,
                        published_at=item.published_at,
                        body=item.canonical_answer,
                    )
                    documents.append(
                        self._document(
                            local_ref=local_ref,
                            entity_type="faq",
                            entity_id=str(item.id),
                            file_name=f"faq-{item.id}.md",
                            content=content,
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
        metadata = [
            f"# {title.strip()}",
            "",
            f"- local_ref: {local_ref}",
            f"- entity_type: {entity_type}",
        ]
        if parent_title:
            metadata.append(f"- parent_document: {parent_title.strip()}")
        if published_at:
            metadata.append(f"- published_at: {published_at.date().isoformat()}")
        if source_url:
            metadata.append(f"- source_url: {source_url}")
        return "\n".join([*metadata, "", "## 본문", "", body.strip(), ""])


class OpenAIVectorIndexService:
    def __init__(
        self,
        app_settings: Settings = settings,
        client: OpenAI | None = None,
        vector_store_id: str | None = None,
        source: RDBVectorDocumentSource | None = None,
    ) -> None:
        if not app_settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for vector indexing")
        self.vector_store_id = vector_store_id or app_settings.openai_vector_store_id
        if not self.vector_store_id:
            raise ValueError("OPENAI_VECTOR_STORE_ID is required for vector indexing")
        self.settings = app_settings
        self.client = client or OpenAI(
            api_key=app_settings.openai_api_key,
            timeout=max(app_settings.vector_search_timeout_seconds, 30),
            max_retries=2,
        )
        self.source = source or RDBVectorDocumentSource()

    def sync(
        self,
        *,
        types: set[str] | None = None,
        limit: int | None = None,
        dry_run: bool = False,
        delete_stale: bool = False,
        force: bool = False,
        batch_size: int = 50,
    ) -> VectorSyncReport:
        documents = self.source.list_documents(types, limit)
        report = VectorSyncReport(
            vector_store_id=self.vector_store_id,
            eligible=len(documents),
            dry_run=dry_run,
        )
        with SessionLocal() as session:
            existing = {
                item.local_ref: item
                for item in session.scalars(
                    select(VectorIndexItem).where(
                        VectorIndexItem.vector_store_id == self.vector_store_id
                    )
                )
            }
        changed: list[tuple[VectorDocument, VectorIndexItem | None]] = []
        for document in documents:
            item = existing.get(document.local_ref)
            if (
                not force
                and item is not None
                and item.content_hash == document.content_hash
                and item.index_status == "completed"
            ):
                report.unchanged += 1
            else:
                changed.append((document, item))
                if item is None or item.index_status == "deleted":
                    report.created += 1
                else:
                    report.updated += 1
        current_refs = {document.local_ref for document in documents}
        stale = (
            [
                item
                for ref, item in existing.items()
                if ref not in current_refs and item.index_status != "deleted"
            ]
            if delete_stale and limit is None
            else []
        )
        report.deleted = len(stale)
        if dry_run:
            return report

        for batch in self._chunks(changed, max(1, min(batch_size, 500))):
            self._sync_batch(batch, report)
        for item in existing.values():
            cleanup_file_id = (item.metadata_json or {}).get("cleanup_file_id")
            if item.index_status != "completed" or not cleanup_file_id:
                continue
            try:
                self._delete_remote_file(str(cleanup_file_id))
                self._clear_cleanup_error(item.local_ref)
            except Exception as exc:  # noqa: BLE001
                report.failed += 1
                self._record_cleanup_error(item.local_ref, str(cleanup_file_id), exc)
        for item in stale:
            try:
                self._delete_remote_file(item.openai_file_id)
                with SessionLocal.begin() as session:
                    current = session.get(VectorIndexItem, item.id)
                    if current:
                        current.index_status = "deleted"
                        current.last_error = None
                        current.updated_at = utcnow()
            except Exception as exc:  # noqa: BLE001
                report.failed += 1
                self._record_error(item.local_ref, None, exc)
        return report

    def _sync_batch(
        self,
        batch: list[tuple[VectorDocument, VectorIndexItem | None]],
        report: VectorSyncReport,
    ) -> None:
        uploads: list[tuple[VectorDocument, VectorIndexItem | None, str]] = []
        for document, existing in batch:
            try:
                payload = io.BytesIO(document.content.encode("utf-8"))
                payload.name = document.file_name
                uploaded = self.client.files.create(file=payload, purpose="assistants")
                uploads.append((document, existing, uploaded.id))
            except Exception as exc:  # noqa: BLE001
                report.failed += 1
                self._record_error(document.local_ref, document, exc)
        if not uploads:
            return
        try:
            self.client.vector_stores.file_batches.create_and_poll(
                self.vector_store_id,
                files=[
                    {"file_id": file_id, "attributes": document.attributes}
                    for document, _existing, file_id in uploads
                ],
            )
        except Exception as exc:  # noqa: BLE001
            report.failed += len(uploads)
            for document, _existing, file_id in uploads:
                self._cleanup_uploaded_file(file_id)
                self._record_error(document.local_ref, document, exc)
            return
        for document, existing, file_id in uploads:
            try:
                remote = self.client.vector_stores.files.retrieve(
                    file_id,
                    vector_store_id=self.vector_store_id,
                )
                if remote.status != "completed":
                    raise RuntimeError(f"vector file status={remote.status}")
                self._save_completed(document, file_id, getattr(remote, "usage_bytes", None))
                if existing and existing.openai_file_id and existing.openai_file_id != file_id:
                    try:
                        self._delete_remote_file(existing.openai_file_id)
                    except Exception as exc:  # noqa: BLE001
                        report.failed += 1
                        self._record_cleanup_error(document.local_ref, existing.openai_file_id, exc)
            except Exception as exc:  # noqa: BLE001
                report.failed += 1
                self._cleanup_uploaded_file(file_id)
                self._record_error(document.local_ref, document, exc)

    def _save_completed(self, document: VectorDocument, file_id: str, usage_bytes: int | None) -> None:
        with SessionLocal.begin() as session:
            item = session.scalar(
                select(VectorIndexItem).where(
                    VectorIndexItem.vector_store_id == self.vector_store_id,
                    VectorIndexItem.local_ref == document.local_ref,
                )
            )
            if item is None:
                item = VectorIndexItem(
                    vector_store_id=self.vector_store_id,
                    local_ref=document.local_ref,
                    entity_type=document.entity_type,
                    entity_id=document.entity_id,
                    file_name=document.file_name,
                    content_hash=document.content_hash,
                )
                session.add(item)
            item.openai_file_id = file_id
            item.file_name = document.file_name
            item.content_hash = document.content_hash
            item.source_updated_at = document.source_updated_at
            item.index_status = "completed"
            item.metadata_json = document.attributes
            item.usage_bytes = usage_bytes
            item.indexed_at = utcnow()
            item.last_error = None

    def _record_error(
        self,
        local_ref: str,
        document: VectorDocument | None,
        exc: Exception,
        file_id: str | None = None,
    ) -> None:
        with SessionLocal.begin() as session:
            item = session.scalar(
                select(VectorIndexItem).where(
                    VectorIndexItem.vector_store_id == self.vector_store_id,
                    VectorIndexItem.local_ref == local_ref,
                )
            )
            if item is None and document is not None:
                item = VectorIndexItem(
                    vector_store_id=self.vector_store_id,
                    local_ref=document.local_ref,
                    entity_type=document.entity_type,
                    entity_id=document.entity_id,
                    file_name=document.file_name,
                    content_hash=document.content_hash,
                )
                session.add(item)
            if item:
                if item.index_status == "completed" and item.openai_file_id:
                    item.last_error = f"update failed; serving previous index: {exc}"[:2000]
                    item.updated_at = utcnow()
                    return
                item.openai_file_id = file_id or item.openai_file_id
                item.index_status = "failed"
                item.last_error = str(exc)[:2000]

    def _record_cleanup_error(self, local_ref: str, file_id: str, exc: Exception) -> None:
        with SessionLocal.begin() as session:
            item = session.scalar(
                select(VectorIndexItem).where(
                    VectorIndexItem.vector_store_id == self.vector_store_id,
                    VectorIndexItem.local_ref == local_ref,
                )
            )
            if item:
                item.last_error = f"old file cleanup failed: {exc}"[:2000]
                item.metadata_json = {**(item.metadata_json or {}), "cleanup_file_id": file_id}

    def _clear_cleanup_error(self, local_ref: str) -> None:
        with SessionLocal.begin() as session:
            item = session.scalar(
                select(VectorIndexItem).where(
                    VectorIndexItem.vector_store_id == self.vector_store_id,
                    VectorIndexItem.local_ref == local_ref,
                )
            )
            if item:
                metadata = dict(item.metadata_json or {})
                metadata.pop("cleanup_file_id", None)
                item.metadata_json = metadata
                item.last_error = None

    def _cleanup_uploaded_file(self, file_id: str) -> None:
        with suppress(Exception):
            self.client.vector_stores.files.delete(
                file_id,
                vector_store_id=self.vector_store_id,
            )
        with suppress(Exception):
            self.client.files.delete(file_id)

    def _delete_remote_file(self, file_id: str | None) -> None:
        if not file_id:
            return
        self.client.vector_stores.files.delete(file_id, vector_store_id=self.vector_store_id)
        self.client.files.delete(file_id)

    @staticmethod
    def _chunks(
        items: list[tuple[VectorDocument, VectorIndexItem | None]],
        size: int,
    ) -> Iterable[list[tuple[VectorDocument, VectorIndexItem | None]]]:
        for index in range(0, len(items), size):
            yield items[index:index + size]


def vector_index_status(vector_store_id: str | None = None) -> dict[str, Any]:
    init_database()
    target = vector_store_id or settings.openai_vector_store_id
    if not target:
        return {
            "configured": False,
            "vector_store_id": None,
            "counts": {},
            "completed_by_type": {},
            "usage_bytes": 0,
            "items_with_errors": 0,
            "last_indexed_at": None,
        }
    with SessionLocal() as session:
        items = list(
            session.scalars(
                select(VectorIndexItem).where(VectorIndexItem.vector_store_id == target)
            )
        )
    counts: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for item in items:
        counts[item.index_status] = counts.get(item.index_status, 0) + 1
        if item.index_status == "completed":
            by_type[item.entity_type] = by_type.get(item.entity_type, 0) + 1
    return {
        "configured": True,
        "vector_store_id": target,
        "counts": counts,
        "completed_by_type": by_type,
        "usage_bytes": sum(item.usage_bytes or 0 for item in items),
        "items_with_errors": sum(bool(item.last_error) for item in items),
        "last_indexed_at": max(
            (item.indexed_at.isoformat() for item in items if item.indexed_at),
            default=None,
        ),
    }
