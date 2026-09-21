from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from .catalog import catalog
from .config import Settings, settings
from .database import SessionLocal, init_database
from .models import (
    AttachmentContent,
    Container,
    FAQCandidate,
    PreservativeGuide,
    PublicDocument,
    ServiceLocation,
    SiteRoute,
    TaxonomyTerm,
)
from .normalization import normalize_search_text
from .schemas import TestInfo
from .search_details import SearchDetails
from .search_lexical import LexicalSearch
from .search_ranking import SearchRanking
from .search_rows import SearchRows
from .search_types import SEARCH_TYPES, SearchHit, _AttachmentSearchRow, _DocumentSearchRow
from .vector_search import (
    VECTOR_ENTITY_TYPES,
    VectorSearchProvider,
    build_vector_search_provider,
)


class PublicDataSearch(SearchRanking, SearchRows, LexicalSearch, SearchDetails):
    def __init__(
        self,
        app_settings: Settings = settings,
        vector_provider: VectorSearchProvider | None = None,
        *,
        session_factory=None,
        app_catalog=None,
    ) -> None:
        self.session_factory = session_factory or SessionLocal
        self.catalog = app_catalog or catalog
        init_database(self.session_factory.kw["bind"])
        self.settings = app_settings
        self.vector_provider = vector_provider or build_vector_search_provider(
            app_settings, session_factory=self.session_factory
        )
        self.vector_search_calls = 0
        self.vector_search_errors = 0
        self.last_vector_error: str | None = None
        self._attachment_cache_lock = threading.Lock()
        self._attachment_cache_signature: (
            tuple[
                int,
                int,
                datetime | None,
                datetime | None,
            ]
            | None
        ) = None
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

    def search_lexical(
        self,
        query: str,
        types: Iterable[str] | None = None,
        limit: int = 10,
        *,
        test_candidates: list[TestInfo] | tuple[TestInfo, ...] | None = None,
    ) -> list[SearchHit]:
        """Search the trusted local index without an external vector request."""

        return self._search_lexical(
            query,
            types,
            max(1, min(limit, 50)),
            test_candidates=test_candidates,
        )

    def recent_documents(
        self,
        required_title_terms: Iterable[str],
        *,
        document_type: str | None = None,
        limit: int = 3,
    ) -> list[SearchHit]:
        """Return locally indexed documents after filtering, then sorting by publication date."""

        terms = [normalize_search_text(term) for term in required_title_terms if term.strip()]
        rows = [
            row
            for row in self._document_search_rows()
            if (document_type is None or row.document_type == document_type)
            and all(term in row.normalized_title for term in terms)
        ]
        rows.sort(key=lambda row: row.updated_at.timestamp() if row.updated_at else 0.0, reverse=True)
        return [
            SearchHit(
                ref=f"document:{row.document_id}",
                entity_type="document",
                entity_id=str(row.document_id),
                title=row.title,
                snippet=row.snippet,
                source_url=row.source_url,
                updated_at=self._date(row.updated_at),
                score=100.0,
                metadata={"document_type": row.document_type, "board_id": row.board_id},
            )
            for row in rows[: max(1, min(limit, 50))]
        ]

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

    def status(self) -> dict[str, Any]:
        from .vector_index import vector_index_status

        index_status = vector_index_status(self.settings)
        with self.session_factory() as session:
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


public_search = PublicDataSearch()
