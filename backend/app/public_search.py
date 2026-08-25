from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from .catalog import catalog
from .config import Settings, settings
from .database import SessionLocal, init_database
from .models import (
    AttachmentChunk,
    AttachmentContent,
    Container,
    ContainerAlias,
    ContainerTestMention,
    DocumentAttachment,
    FAQCandidate,
    PreservativeGuide,
    PublicDocument,
    ServiceLocation,
    SiteRoute,
    TaxonomyTerm,
    Test,
    TestTaxonomyLink,
)
from .normalization import clean_text, normalize_search_text
from .schemas import TestInfo
from .vector_search import (
    VECTOR_ENTITY_TYPES,
    VectorSearchProvider,
    build_vector_search_provider,
)

SEARCH_TYPES = {
    "test",
    "document",
    "container",
    "preservative",
    "location",
    "route",
    "taxonomy",
    "faq",
    "attachment",
}
STOP_WORDS = {
    "알려줘",
    "찾아줘",
    "보여줘",
    "안내",
    "검색",
    "관련",
    "정보",
    "어디",
    "뭐야",
    "무엇",
    "홈페이지",
    "검사",
    "검사는",
    "검사가",
    "검사를",
    "검사에",
    "검사에서",
    "검사항목",
    "어떤",
    "어느",
    "있나요",
    "있습니까",
    "인가요",
    "가능한가요",
    "가능한지",
    "되나요",
    "해주세요",
    "관련된",
    "관련한",
    "페이지",
    "메뉴",
    "링크",
    "문의",
    "내용",
    "문서",
    "다운로드",
}


@dataclass(frozen=True)
class SearchHit:
    ref: str
    entity_type: str
    entity_id: str
    title: str
    snippet: str | None
    source_url: str | None
    updated_at: str | None
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _AttachmentSearchRow:
    attachment_id: int
    content_id: int
    file_name: str
    normalized_file_name: str
    file_type: str
    download_url: str
    document_id: int
    document_title: str
    document_source_url: str
    document_normalized_title: str
    text: str
    normalized_text: str
    page_number: int | None
    section_label: str | None
    extracted_at: datetime | None


@dataclass(frozen=True)
class _DocumentSearchRow:
    document_id: int
    title: str
    normalized_title: str
    normalized_extra: str
    snippet: str | None
    source_url: str
    updated_at: datetime | None
    document_type: str
    board_id: str


class PublicDataSearch:
    def __init__(
        self,
        app_settings: Settings = settings,
        vector_provider: VectorSearchProvider | None = None,
    ) -> None:
        init_database()
        self.settings = app_settings
        self.vector_provider = vector_provider or build_vector_search_provider(app_settings)
        self.vector_search_calls = 0
        self.vector_search_errors = 0
        self.last_vector_error: str | None = None
        self._attachment_cache_lock = threading.Lock()
        self._attachment_cache_signature: tuple[
            int,
            int,
            datetime | None,
            datetime | None,
        ] | None = None
        self._attachment_cache: tuple[_AttachmentSearchRow, ...] = ()
        self._document_cache_lock = threading.Lock()
        self._document_cache_signature: tuple[int, int, datetime | None] | None = None
        self._document_cache: tuple[_DocumentSearchRow, ...] = ()

    def search(
        self,
        query: str,
        types: Iterable[str] | None = None,
        limit: int = 10,
        *,
        test_candidates: list[TestInfo] | tuple[TestInfo, ...] | None = None,
    ) -> list[SearchHit]:
        allowed = set(types or SEARCH_TYPES) & SEARCH_TYPES
        if not allowed:
            return []
        result_limit = max(1, min(limit, 50))
        retrieval_limit = max(result_limit, self.settings.vector_search_max_results)
        lexical_hits = self._search_lexical(
            query,
            allowed,
            retrieval_limit,
            test_candidates=test_candidates,
        )
        vector_types = allowed & VECTOR_ENTITY_TYPES
        if not vector_types or not self.settings.vector_search_configured:
            return lexical_hits[:result_limit]
        self.vector_search_calls += 1
        try:
            vector_hits = self.vector_provider.search(query, vector_types, retrieval_limit)
            self.last_vector_error = None
        except Exception as exc:  # noqa: BLE001
            self.vector_search_errors += 1
            self.last_vector_error = str(exc)[:500]
            return lexical_hits[:result_limit]
        if not vector_hits or self.settings.vector_search_shadow_mode:
            return lexical_hits[:result_limit]
        return self._hybrid_rank(lexical_hits, vector_hits, result_limit)

    def _search_lexical(
        self,
        query: str,
        types: Iterable[str] | None = None,
        limit: int = 10,
        *,
        test_candidates: list[TestInfo] | tuple[TestInfo, ...] | None = None,
    ) -> list[SearchHit]:
        allowed = set(types or SEARCH_TYPES) & SEARCH_TYPES
        if not allowed:
            return []
        normalized = normalize_search_text(query)
        terms = [term for term in normalized.split() if len(term) > 1 and term not in STOP_WORDS]
        hits: list[SearchHit] = []
        if "test" in allowed:
            non_test_intent = any(
                cue in normalized
                for cue in [
                    "공문",
                    "공지",
                    "일정",
                    "변경",
                    "뉴스",
                    "건강",
                    "사회공헌",
                    "자료",
                    "리플릿",
                    "지점",
                    "센터",
                    "주소",
                    "전화",
                    "팩스",
                    "연락처",
                    "메뉴",
                    "페이지",
                    "경로",
                    "링크",
                    "보존제",
                    "24시간뇨",
                    "차광",
                    "문서",
                    "첨부",
                    "파일",
                    "양식",
                    "다운로드",
                ]
            )
            test_base = 55 if non_test_intent else 180
            candidates = (
                list(test_candidates)[: max(limit, 10)]
                if test_candidates is not None
                else catalog.search(query, limit=max(limit, 10))
            )
            for index, item in enumerate(candidates):
                hits.append(
                    SearchHit(
                        ref=f"test:{item.variant_key or item.code}",
                        entity_type="test",
                        entity_id=item.variant_key or item.code,
                        title=item.name,
                        snippet=f"{item.specimen} · {item.method} · 소요일 {item.tat}",
                        source_url=str(item.source_url) if item.source_url else None,
                        updated_at=item.updated_at,
                        score=test_base - index,
                        metadata={
                            "code": item.code,
                            "variant_key": item.variant_key,
                            "specimen": item.specimen,
                            "container": item.container,
                        },
                    )
                )
        with SessionLocal() as session:
            if "faq" in allowed:
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
            if "attachment" in allowed and terms:
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
                    score += self._attachment_intent_boost(
                        normalized, terms, row.file_name, row.document_title
                    )
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
            if "document" in allowed:
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
            if "container" in allowed:
                aliases = self._group_values(
                    session.execute(select(ContainerAlias.container_id, ContainerAlias.alias))
                )
                mentions = self._group_values(
                    session.execute(
                        select(ContainerTestMention.container_id, ContainerTestMention.mention_text)
                    )
                )
                for item in session.scalars(select(Container).where(Container.status == "active")):
                    extra = " ".join(
                        [
                            item.additive or "",
                            item.major_tests_text or "",
                            *aliases.get(item.id, []),
                            *mentions.get(item.id, []),
                        ]
                    )
                    base_score = self._score(normalized, terms, item.name, extra)
                    score = (
                        base_score + self._type_boost(normalized, "container")
                        if base_score or not terms
                        else 0
                    )
                    if score > 0:
                        hits.append(
                            SearchHit(
                                ref=f"container:{item.id}",
                                entity_type="container",
                                entity_id=str(item.id),
                                title=item.name,
                                snippet=self._snippet(item.major_tests_text or item.caution_text),
                                source_url=item.source_url,
                                updated_at=self._date(item.last_seen_at),
                                score=score,
                                metadata={
                                    "additive": item.additive,
                                    "storage": item.storage_text,
                                    "collection_volume": item.collection_volume_text,
                                    "aliases": aliases.get(item.id, []),
                                },
                            )
                        )
            if "preservative" in allowed:
                for item in session.scalars(
                    select(PreservativeGuide).where(PreservativeGuide.status == "active")
                ):
                    guide = " ".join(
                        filter(
                            None,
                            [
                                item.light_protection,
                                item.acetic_acid_50,
                                item.hcl_6n,
                                item.boric_acid_10g,
                                item.sodium_carbonate_5g,
                                item.no_preservative,
                            ],
                        )
                    )
                    base_score = self._score(normalized, terms, item.test_name, guide)
                    score = (
                        base_score + self._type_boost(normalized, "preservative")
                        if base_score or not terms
                        else 0
                    )
                    if score > 0:
                        hits.append(
                            SearchHit(
                                ref=f"preservative:{item.id}",
                                entity_type="preservative",
                                entity_id=str(item.id),
                                title=item.test_name,
                                snippet=guide or None,
                                source_url=item.source_url,
                                updated_at=self._date(item.last_seen_at),
                                score=score,
                                metadata={
                                    "light_protection": item.light_protection,
                                    "acetic_acid_50": item.acetic_acid_50,
                                    "hcl_6n": item.hcl_6n,
                                    "boric_acid_10g": item.boric_acid_10g,
                                    "sodium_carbonate_5g": item.sodium_carbonate_5g,
                                    "no_preservative": item.no_preservative,
                                },
                            )
                        )
            if "location" in allowed:
                for item in session.scalars(
                    select(ServiceLocation).where(ServiceLocation.status == "active")
                ):
                    extra = " ".join(filter(None, [item.region, item.address, item.phone, item.fax]))
                    base_score = self._score(normalized, terms, item.name, extra)
                    score = (
                        base_score + self._type_boost(normalized, "location")
                        if base_score or not terms
                        else 0
                    )
                    if score > 0:
                        hits.append(
                            SearchHit(
                                ref=f"location:{item.id}",
                                entity_type="location",
                                entity_id=str(item.id),
                                title=item.name,
                                snippet=" · ".join(filter(None, [item.address, item.phone])),
                                source_url=item.source_url,
                                updated_at=self._date(item.last_seen_at),
                                score=score,
                                metadata={
                                    "location_type": item.location_type,
                                    "region": item.region,
                                    "address": item.address,
                                    "phone": item.phone,
                                    "fax": item.fax,
                                },
                            )
                        )
            if "route" in allowed:
                for item in session.scalars(select(SiteRoute).where(SiteRoute.status == "active")):
                    base_score = self._score(normalized, terms, item.label, item.path)
                    score = (
                        base_score + self._type_boost(normalized, "route") if base_score or not terms else 0
                    )
                    if score > 0:
                        absolute = (
                            item.path
                            if item.path.startswith("http")
                            else f"https://www.scllab.co.kr{item.path}"
                        )
                        hits.append(
                            SearchHit(
                                ref=f"route:{item.id}",
                                entity_type="route",
                                entity_id=str(item.id),
                                title=item.label,
                                snippet=item.path,
                                source_url=absolute,
                                updated_at=self._date(item.last_seen_at),
                                score=score,
                                metadata={
                                    "path": item.path,
                                    "parent_path": item.parent_path,
                                    "depth": item.depth,
                                },
                            )
                        )
            if "taxonomy" in allowed or "test" in allowed:
                for item in session.scalars(select(TaxonomyTerm)):
                    score = self._score(normalized, terms, item.name, item.description or "")
                    if score > 0:
                        if "taxonomy" in allowed:
                            hits.append(
                                SearchHit(
                                    ref=f"taxonomy:{item.id}",
                                    entity_type="taxonomy",
                                    entity_id=str(item.id),
                                    title=item.name,
                                    snippet=self._snippet(item.description),
                                    source_url=item.source_url,
                                    updated_at=None,
                                    score=score,
                                    metadata={"taxonomy": item.taxonomy},
                                )
                            )
                        if item.taxonomy == "disease_group" and "test" in allowed:
                            linked_tests = session.execute(
                                select(TestTaxonomyLink, Test)
                                .join(Test, Test.id == TestTaxonomyLink.test_id)
                                .where(TestTaxonomyLink.taxonomy_term_id == item.id, Test.status == "active")
                                .order_by(Test.name)
                                .limit(30)
                            )
                            for index, (link, test) in enumerate(linked_tests):
                                variants = [
                                    candidate
                                    for candidate in catalog.search(test.source_test_code, limit=5)
                                    if candidate.code == test.source_test_code
                                ]
                                if not variants:
                                    continue
                                candidate = variants[0]
                                hits.append(
                                    SearchHit(
                                        ref=f"test:{candidate.variant_key or candidate.code}",
                                        entity_type="test",
                                        entity_id=candidate.variant_key or candidate.code,
                                        title=candidate.name,
                                        snippet=f"{item.name} 큐레이션 · {candidate.specimen} · {candidate.method}",
                                        source_url=str(candidate.source_url)
                                        if candidate.source_url
                                        else None,
                                        updated_at=candidate.updated_at,
                                        score=165 - index * 0.01,
                                        metadata={
                                            "code": candidate.code,
                                            "variant_key": candidate.variant_key,
                                            "disease_group": item.name,
                                            "curation_source": link.source,
                                            "verified": link.verified,
                                        },
                                    )
                                )
        deduplicated: dict[str, SearchHit] = {}
        for hit in hits:
            if hit.ref not in deduplicated or hit.score > deduplicated[hit.ref].score:
                deduplicated[hit.ref] = hit
        ranked = sorted(deduplicated.values(), key=lambda hit: (-hit.score, hit.entity_type, hit.title))
        return ranked[: max(1, min(limit, 50))]

    def _attachment_search_rows(self) -> tuple[_AttachmentSearchRow, ...]:
        with SessionLocal() as session:
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
        with SessionLocal() as session:
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

    def get(self, entity_type: str, entity_id: str) -> SearchHit | None:
        if entity_type == "test":
            item = catalog.get(None, entity_id) or catalog.get(entity_id)
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
            with SessionLocal() as session:
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
            with SessionLocal() as session:
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
        with SessionLocal() as session:
            item = session.get(model, int(entity_id))
            if item is None:
                return None
            if hasattr(item, "status") and item.status != "active":
                return None
        candidates = self._search_lexical(self._title_for(item, entity_type), [entity_type], 50)
        return next((hit for hit in candidates if hit.entity_id == entity_id), None)

    def _hybrid_rank(
        self,
        lexical_hits: list[SearchHit],
        vector_hits: list[Any],
        limit: int,
    ) -> list[SearchHit]:
        fused: dict[str, tuple[SearchHit, float]] = {}
        for rank, hit in enumerate(lexical_hits, start=1):
            score = 1 / (60 + rank) + min(max(hit.score, 0), 200) / 10_000
            fused[hit.ref] = (hit, score)
        for rank, vector_hit in enumerate(vector_hits, start=1):
            trusted = self.get_ref(vector_hit.ref)
            if trusted is None:
                continue
            vector_score = 0.9 / (60 + rank) + max(0.0, min(vector_hit.score, 1.0)) * 0.005
            existing = fused.get(trusted.ref)
            base = existing[0] if existing else trusted
            metadata = {
                **base.metadata,
                "retrieval": "hybrid" if existing else "vector",
                "vector_score": round(float(vector_hit.score), 6),
                "vector_file_id": vector_hit.file_id,
            }
            merged = replace(
                base,
                snippet=vector_hit.snippet or base.snippet,
                metadata=metadata,
            )
            fused[trusted.ref] = (merged, (existing[1] if existing else 0.0) + vector_score)
        ranked = sorted(
            (replace(hit, score=score * 1000) for hit, score in fused.values()),
            key=lambda hit: (-hit.score, hit.entity_type, hit.title),
        )
        return ranked[:limit]

    def get_ref(self, ref: str) -> SearchHit | None:
        if ":" not in ref:
            return None
        entity_type, entity_id = ref.split(":", 1)
        return self.get(entity_type, entity_id)

    def find_route(self, path_or_ref: str) -> SearchHit | None:
        if path_or_ref.startswith("route:"):
            return self.get_ref(path_or_ref)
        with SessionLocal() as session:
            item = session.scalar(select(SiteRoute).where(SiteRoute.path == path_or_ref))
            return self.get("route", str(item.id)) if item else None

    def status(self) -> dict[str, Any]:
        from .vector_index import vector_index_status

        index_status = vector_index_status(self.settings.openai_vector_store_id)
        with SessionLocal() as session:
            return {
                "documents": session.scalar(select(func.count()).select_from(PublicDocument)) or 0,
                "containers": session.scalar(select(func.count()).select_from(Container)) or 0,
                "preservatives": session.scalar(select(func.count()).select_from(PreservativeGuide)) or 0,
                "locations": session.scalar(select(func.count()).select_from(ServiceLocation)) or 0,
                "routes": session.scalar(select(func.count()).select_from(SiteRoute)) or 0,
                "taxonomy_terms": session.scalar(select(func.count()).select_from(TaxonomyTerm)) or 0,
                "published_faqs": session.scalar(
                    select(func.count()).select_from(FAQCandidate).where(FAQCandidate.status == "published")
                )
                or 0,
                "extracted_attachments": session.scalar(
                    select(func.count())
                    .select_from(AttachmentContent)
                    .where(AttachmentContent.extraction_status == "extracted")
                )
                or 0,
                "vector_search_enabled": self.settings.vector_search_enabled,
                "vector_search_configured": self.settings.vector_search_configured,
                "vector_search_shadow_mode": self.settings.vector_search_shadow_mode,
                "vector_search_calls": self.vector_search_calls,
                "vector_search_errors": self.vector_search_errors,
                "last_vector_error": self.last_vector_error,
                "vector_index_counts": index_status.get("counts", {}),
                "vector_index_completed_by_type": index_status.get("completed_by_type", {}),
                "vector_index_usage_bytes": index_status.get("usage_bytes", 0),
                "vector_index_items_with_errors": index_status.get("items_with_errors", 0),
                "vector_index_last_synced_at": index_status.get("last_indexed_at"),
            }

    def prompt_snapshot(self, hits: list[SearchHit]) -> str:
        if not hits:
            return "검색된 공개 데이터가 없습니다."
        return "\n".join(
            f"- ref={hit.ref} | type={hit.entity_type} | title={hit.title} | "
            f"detail={(hit.snippet or '-')[:160]} | "
            f"url={hit.source_url or '-'} | updated_at={hit.updated_at or '-'}"
            for hit in hits
        )

    @staticmethod
    def _score(query: str, terms: list[str], title: str, extra: str) -> float:
        return PublicDataSearch._score_normalized(
            query,
            terms,
            normalize_search_text(title),
            normalize_search_text(extra),
        )

    @staticmethod
    def _score_normalized(
        query: str,
        terms: list[str],
        title_n: str,
        extra_n: str,
    ) -> float:
        if not query:
            return 0
        score = 0.0
        if query == title_n:
            score += 120
        elif query in title_n:
            score += 70
        for term in terms:
            if term == title_n:
                score += 45
            elif term in title_n:
                score += 24
            elif term in extra_n:
                score += 7
        return score

    @staticmethod
    def _type_boost(query: str, entity_type: str, subtype: str | None = None) -> float:
        cues = {
            "document": ["공문", "공지", "일정", "변경", "자료", "리플릿", "뉴스", "건강", "사회공헌"],
            "container": ["용기", "튜브", "채취", "보관"],
            "preservative": ["보존제", "24시간뇨", "차광"],
            "location": ["지점", "센터", "주소", "전화", "팩스", "연락처"],
            "route": ["메뉴", "페이지", "어디", "경로", "링크", "로그인"],
            "faq": ["자주", "FAQ", "문의", "질문"],
            "attachment": ["첨부", "파일", "PDF", "공문", "자료", "양식", "다운로드"],
        }
        boost = 90 if any(cue in query for cue in cues.get(entity_type, [])) else 0
        if entity_type == "document" and subtype == "official_faq":
            boost += 40
        if subtype and subtype.replace("_", " ") in query:
            boost += 10
        return boost

    @staticmethod
    def _attachment_intent_boost(query: str, terms: list[str], file_name: str, document_title: str) -> float:
        """Prefer the general SCL request form for an otherwise generic download query.

        Without this tie-breaker dozens of specialist referral forms receive the
        same score and alphabetical ordering can surface an unrelated hospital form.
        A named test (for example, AMH) remains more specific and gets no boost.
        """
        generic_terms = {"검사의뢰서", "의뢰서", "다운로드", "양식", "파일"}
        specific_terms = [term for term in terms if term not in generic_terms]
        combined = normalize_search_text(f"{file_name} {document_title}").replace(" ", "")
        if (
            not specific_terms
            and ("검사의뢰서" in query.replace(" ", "") or "의뢰서" in query)
            and "일반검사의뢰서" in combined
        ):
            return 60
        return 0

    @staticmethod
    def _snippet(value: str | None, limit: int = 240) -> str | None:
        text = clean_text(value)
        return text[:limit] if text else None

    @staticmethod
    def _date(value: datetime | None) -> str | None:
        return value.date().isoformat() if value else None

    @staticmethod
    def _group_values(rows: Any) -> dict[int, list[str]]:
        grouped: dict[int, list[str]] = {}
        for owner_id, value in rows:
            grouped.setdefault(owner_id, []).append(value)
        return grouped

    @staticmethod
    def _title_for(item: Any, entity_type: str) -> str:
        if entity_type == "document":
            return item.title
        if entity_type == "preservative":
            return item.test_name
        if entity_type == "route":
            return item.label
        return item.name


public_search = PublicDataSearch()
