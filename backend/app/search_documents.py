from __future__ import annotations

from sqlalchemy import select

from .models import (
    FAQCandidate,
)
from .search_types import SearchHit


class DocumentSearchHandlers:
    def _search_faq(self, normalized, terms, session):
        hits: list[SearchHit] = []
        for item in session.scalars(select(FAQCandidate).where(FAQCandidate.status == "published")):
            score = self._score(normalized, terms, item.canonical_question, item.canonical_answer)
            if score > 0:
                hits.append(
                    SearchHit(
                        ref=f"faq:{item.id}",
                        entity_type="faq",
                        entity_id=str(item.id),
                        title=item.canonical_question,
                        snippet=self._snippet(item.canonical_answer),
                        source_url=None,
                        updated_at=self._date(item.published_at or item.updated_at),
                        score=score + 35,
                        metadata={
                            "domain": item.domain,
                            "sub_intent": item.sub_intent,
                            "source_refs": item.source_refs,
                        },
                    )
                )
        return hits

    def _search_attachment(self, normalized, terms):
        hits: list[SearchHit] = []
        best_attachments: dict[int, SearchHit] = {}
        for row in self._attachment_search_rows():
            if not any(
                term in row.normalized_text
                or term in row.normalized_file_name
                or term in row.document_normalized_title
                for term in terms
            ):
                continue
            score = self._score_normalized(
                normalized,
                terms,
                row.normalized_file_name,
                f"{row.document_normalized_title} {row.normalized_text}",
            )
            score += self._attachment_intent_boost(normalized, terms, row.file_name, row.document_title)
            if score <= 0:
                continue
            hit = SearchHit(
                ref=f"attachment:{row.attachment_id}",
                entity_type="attachment",
                entity_id=str(row.attachment_id),
                title=row.file_name,
                snippet=row.text[:240] or None,
                source_url=row.document_source_url,
                updated_at=self._date(row.extracted_at),
                score=score + 25,
                metadata={
                    "document_id": row.document_id,
                    "document_title": row.document_title,
                    "file_type": row.file_type,
                    "download_url": row.download_url,
                    "page_number": row.page_number,
                    "section_label": row.section_label,
                },
            )
            previous = best_attachments.get(row.attachment_id)
            if previous is None or hit.score > previous.score:
                best_attachments[row.attachment_id] = hit
        hits.extend(best_attachments.values())
        return hits

    def _search_document(self, normalized, terms):
        hits: list[SearchHit] = []
        for item in self._document_search_rows():
            base_score = self._score_normalized(
                normalized,
                terms,
                item.normalized_title,
                item.normalized_extra,
            )
            score = (
                base_score + self._type_boost(normalized, "document", item.document_type)
                if base_score or not terms
                else 0
            )
            if base_score and item.document_type == "official_faq":
                score += 100
            if score > 0:
                hits.append(
                    SearchHit(
                        ref=f"document:{item.document_id}",
                        entity_type="document",
                        entity_id=str(item.document_id),
                        title=item.title,
                        snippet=item.snippet,
                        source_url=item.source_url,
                        updated_at=self._date(item.updated_at),
                        score=score,
                        metadata={"document_type": item.document_type, "board_id": item.board_id},
                    )
                )
        return hits
