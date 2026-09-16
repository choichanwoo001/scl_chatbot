from __future__ import annotations

from sqlalchemy import func, select

from .models import (
    AttachmentChunk,
    AttachmentContent,
    DocumentAttachment,
    PublicDocument,
)
from .normalization import normalize_search_text
from .search_types import _AttachmentSearchRow, _DocumentSearchRow


class SearchRows:
    def _attachment_search_rows(self) -> tuple[_AttachmentSearchRow, ...]:
        with self.session_factory() as session:
            count, max_id = session.execute(
                select(func.count(AttachmentChunk.id), func.max(AttachmentChunk.id))
            ).one()
            latest_extracted = session.scalar(select(func.max(AttachmentContent.extracted_at)))
            latest_document = session.scalar(select(func.max(PublicDocument.last_seen_at)))
            signature = (int(count or 0), int(max_id or 0), latest_extracted, latest_document)
            if signature == self._attachment_cache_signature:
                return self._attachment_cache

            with self._attachment_cache_lock:
                if signature == self._attachment_cache_signature:
                    return self._attachment_cache
                statement = (
                    select(
                        AttachmentChunk.id,
                        AttachmentChunk.text,
                        AttachmentChunk.normalized_text,
                        AttachmentChunk.page_number,
                        AttachmentChunk.section_label,
                        AttachmentContent.id,
                        AttachmentContent.extracted_at,
                        DocumentAttachment.id,
                        DocumentAttachment.file_name,
                        DocumentAttachment.file_type,
                        DocumentAttachment.download_url,
                        PublicDocument.id,
                        PublicDocument.title,
                        PublicDocument.normalized_title,
                        PublicDocument.source_url,
                    )
                    .join(AttachmentContent, AttachmentContent.id == AttachmentChunk.attachment_content_id)
                    .join(DocumentAttachment, DocumentAttachment.id == AttachmentContent.attachment_id)
                    .join(PublicDocument, PublicDocument.id == DocumentAttachment.document_id)
                    .where(
                        AttachmentContent.extraction_status == "extracted",
                        DocumentAttachment.status == "active",
                        PublicDocument.status == "active",
                    )
                )
                rows = tuple(
                    _AttachmentSearchRow(
                        attachment_id=attachment_id,
                        content_id=content_id,
                        file_name=file_name,
                        normalized_file_name=normalize_search_text(file_name),
                        file_type=file_type,
                        download_url=download_url,
                        document_id=document_id,
                        document_title=document_title,
                        document_source_url=document_source_url,
                        document_normalized_title=document_normalized_title,
                        text=text,
                        normalized_text=normalized_text,
                        page_number=page_number,
                        section_label=section_label,
                        extracted_at=extracted_at,
                    )
                    for (
                        _chunk_id,
                        text,
                        normalized_text,
                        page_number,
                        section_label,
                        content_id,
                        extracted_at,
                        attachment_id,
                        file_name,
                        file_type,
                        download_url,
                        document_id,
                        document_title,
                        document_normalized_title,
                        document_source_url,
                    ) in session.execute(statement)
                )
                self._attachment_cache = rows
                self._attachment_cache_signature = signature
                return rows

    def _document_search_rows(self) -> tuple[_DocumentSearchRow, ...]:
        with self.session_factory() as session:
            count, max_id, latest_seen = session.execute(
                select(
                    func.count(PublicDocument.id),
                    func.max(PublicDocument.id),
                    func.max(PublicDocument.last_seen_at),
                ).where(PublicDocument.status == "active")
            ).one()
            signature = (int(count or 0), int(max_id or 0), latest_seen)
            if signature == self._document_cache_signature:
                return self._document_cache

            with self._document_cache_lock:
                if signature == self._document_cache_signature:
                    return self._document_cache
                statement = select(
                    PublicDocument.id,
                    PublicDocument.title,
                    PublicDocument.normalized_title,
                    PublicDocument.summary,
                    PublicDocument.body_text,
                    PublicDocument.source_url,
                    PublicDocument.published_at,
                    PublicDocument.last_seen_at,
                    PublicDocument.document_type,
                    PublicDocument.board_id,
                ).where(PublicDocument.status == "active")
                rows = tuple(
                    _DocumentSearchRow(
                        document_id=document_id,
                        title=title,
                        normalized_title=normalized_title,
                        normalized_extra=normalize_search_text(f"{summary or ''} {body_text or ''}"),
                        snippet=self._snippet(summary or body_text),
                        source_url=source_url,
                        updated_at=published_at or last_seen_at,
                        document_type=document_type,
                        board_id=board_id,
                    )
                    for (
                        document_id,
                        title,
                        normalized_title,
                        summary,
                        body_text,
                        source_url,
                        published_at,
                        last_seen_at,
                        document_type,
                        board_id,
                    ) in session.execute(statement)
                )
                self._document_cache = rows
                self._document_cache_signature = signature
                return rows
