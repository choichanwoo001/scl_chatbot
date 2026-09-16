from __future__ import annotations

from collections.abc import Iterable

from .normalization import normalize_search_text
from .schemas import TestInfo
from .search_catalog import CatalogSearchHandlers
from .search_documents import DocumentSearchHandlers
from .search_navigation import NavigationSearchHandlers
from .search_types import SEARCH_TYPES, STOP_WORDS, SearchHit


class LexicalSearch(DocumentSearchHandlers, CatalogSearchHandlers, NavigationSearchHandlers):
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
            hits.extend(self._search_test(query, normalized, limit, test_candidates))
        if "attachment" in allowed and terms:
            hits.extend(self._search_attachment(normalized, terms))
        if "document" in allowed:
            hits.extend(self._search_document(normalized, terms))
        with self.session_factory() as session:
            handlers = {
                "faq": self._search_faq,
                "container": self._search_container,
                "preservative": self._search_preservative,
                "location": self._search_location,
                "route": self._search_route,
            }
            for kind, handler in handlers.items():
                if kind in allowed:
                    hits.extend(handler(normalized, terms, session))
            if allowed & {"taxonomy", "test"}:
                hits.extend(self._search_taxonomy(normalized, terms, session, allowed))
        deduplicated: dict[str, SearchHit] = {}
        for hit in hits:
            if hit.ref not in deduplicated or hit.score > deduplicated[hit.ref].score:
                deduplicated[hit.ref] = hit
        ranked = sorted(deduplicated.values(), key=lambda hit: (-hit.score, hit.entity_type, hit.title))
        return ranked[: max(1, min(limit, 50))]
