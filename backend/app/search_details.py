from __future__ import annotations

from sqlalchemy import select

from .models import (
    AttachmentContent,
    Container,
    DocumentAttachment,
    FAQCandidate,
    PreservativeGuide,
    PublicDocument,
    ServiceLocation,
    SiteRoute,
    TaxonomyTerm,
)
from .search_types import SearchHit


class SearchDetails:
    def get(self, entity_type: str, entity_id: str) -> SearchHit | None:
        if entity_type == "test":
            item = self.catalog.get(None, entity_id) or self.catalog.get(entity_id)
            if not item:
                return None
            return SearchHit(
                ref=f"test:{entity_id}",
                entity_type="test",
                entity_id=entity_id,
                title=item.name,
                snippet=f"{item.specimen} · {item.method} · {item.tat}",
                source_url=str(item.source_url) if item.source_url else None,
                updated_at=item.updated_at,
                score=0,
                metadata=item.model_dump(mode="json"),
            )
        if entity_type == "faq" and entity_id.isdigit():
            with self.session_factory() as session:
                item = session.get(FAQCandidate, int(entity_id))
                if not item or item.status != "published":
                    return None
                return SearchHit(
                    ref=f"faq:{item.id}",
                    entity_type="faq",
                    entity_id=str(item.id),
                    title=item.canonical_question,
                    snippet=self._snippet(item.canonical_answer),
                    source_url=None,
                    updated_at=self._date(item.published_at or item.updated_at),
                    score=0,
                    metadata={
                        "domain": item.domain,
                        "sub_intent": item.sub_intent,
                        "source_refs": item.source_refs,
                    },
                )
        if entity_type == "attachment" and entity_id.isdigit():
            with self.session_factory() as session:
                row = session.execute(
                    select(DocumentAttachment, PublicDocument, AttachmentContent)
                    .join(PublicDocument, PublicDocument.id == DocumentAttachment.document_id)
                    .outerjoin(AttachmentContent, AttachmentContent.attachment_id == DocumentAttachment.id)
                    .where(DocumentAttachment.id == int(entity_id), DocumentAttachment.status == "active")
                ).first()
                if not row:
                    return None
                attachment, document, content = row
                return SearchHit(
                    ref=f"attachment:{attachment.id}",
                    entity_type="attachment",
                    entity_id=str(attachment.id),
                    title=attachment.file_name,
                    snippet=self._snippet(content.extracted_text if content else None),
                    source_url=document.source_url,
                    updated_at=self._date(content.extracted_at if content else attachment.last_seen_at),
                    score=0,
                    metadata={
                        "document_id": document.id,
                        "document_title": document.title,
                        "file_type": attachment.file_type,
                        "download_url": attachment.download_url,
                    },
                )
        model_map = {
            "document": PublicDocument,
            "container": Container,
            "preservative": PreservativeGuide,
            "location": ServiceLocation,
            "route": SiteRoute,
            "taxonomy": TaxonomyTerm,
        }
        model = model_map.get(entity_type)
        if model is None or not entity_id.isdigit():
            return None
        with self.session_factory() as session:
            item = session.get(model, int(entity_id))
            if item is None:
                return None
            if hasattr(item, "status") and item.status != "active":
                return None
        candidates = self._search_lexical(self._title_for(item, entity_type), [entity_type], 50)
        return next((hit for hit in candidates if hit.entity_id == entity_id), None)

    def get_ref(self, ref: str) -> SearchHit | None:
        if ":" not in ref:
            return None
        entity_type, entity_id = ref.split(":", 1)
        return self.get(entity_type, entity_id)

    def find_route(self, path_or_ref: str) -> SearchHit | None:
        if path_or_ref.startswith("route:"):
            return self.get_ref(path_or_ref)
        with self.session_factory() as session:
            item = session.scalar(select(SiteRoute).where(SiteRoute.path == path_or_ref))
            return self.get("route", str(item.id)) if item else None
