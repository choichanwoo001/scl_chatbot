from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import OpenAI
from sqlalchemy import select

from .config import Settings, settings
from .database import SessionLocal, init_database
from .models import VectorIndexItem

VECTOR_ENTITY_TYPES = {"document", "attachment", "faq"}


@dataclass(frozen=True)
class VectorSearchHit:
    ref: str
    entity_type: str
    entity_id: str
    score: float
    snippet: str | None
    file_id: str
    filename: str
    attributes: dict[str, str | float | bool] = field(default_factory=dict)


class VectorSearchProvider(Protocol):
    def search(
        self,
        query: str,
        types: set[str] | None = None,
        limit: int | None = None,
    ) -> list[VectorSearchHit]: ...


class DisabledVectorSearchProvider:
    def search(
        self,
        query: str,
        types: set[str] | None = None,
        limit: int | None = None,
    ) -> list[VectorSearchHit]:
        return []


class OpenAIVectorSearchProvider:
    def __init__(self, app_settings: Settings = settings, client: OpenAI | None = None) -> None:
        if not app_settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for vector search")
        if not app_settings.openai_vector_store_id:
            raise ValueError("OPENAI_VECTOR_STORE_ID is required for vector search")
        init_database()
        self.settings = app_settings
        self.client = client or OpenAI(
            api_key=app_settings.openai_api_key,
            timeout=app_settings.vector_search_timeout_seconds,
            max_retries=1,
        )

    def search(
        self,
        query: str,
        types: set[str] | None = None,
        limit: int | None = None,
    ) -> list[VectorSearchHit]:
        selected_types = (set(types) if types else set(VECTOR_ENTITY_TYPES)) & VECTOR_ENTITY_TYPES
        if not query.strip() or not selected_types:
            return []
        max_results = max(1, min(limit or self.settings.vector_search_max_results, 50))
        response = self.client.vector_stores.search(
            self.settings.openai_vector_store_id,
            query=query,
            filters={"type": "in", "key": "entity_type", "value": sorted(selected_types)},
            max_num_results=max_results,
            rewrite_query=True,
        )
        rows = [row for row in response.data if row.score >= self.settings.vector_search_min_score]
        if not rows:
            return []
        file_ids = [row.file_id for row in rows]
        with SessionLocal() as session:
            mappings = {
                item.openai_file_id: item
                for item in session.scalars(
                    select(VectorIndexItem).where(
                        VectorIndexItem.vector_store_id == self.settings.openai_vector_store_id,
                        VectorIndexItem.openai_file_id.in_(file_ids),
                        VectorIndexItem.index_status == "completed",
                    )
                )
                if item.openai_file_id
            }
        hits: list[VectorSearchHit] = []
        for row in rows:
            mapping = mappings.get(row.file_id)
            if mapping is None or mapping.entity_type not in selected_types:
                continue
            hits.append(
                VectorSearchHit(
                    ref=mapping.local_ref,
                    entity_type=mapping.entity_type,
                    entity_id=mapping.entity_id,
                    score=float(row.score),
                    snippet=self._content_text(row.content),
                    file_id=row.file_id,
                    filename=row.filename,
                    attributes=dict(row.attributes or {}),
                )
            )
        return hits[:max_results]

    @staticmethod
    def _content_text(content: list[Any]) -> str | None:
        parts: list[str] = []
        for item in content:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            if text:
                parts.append(str(text).strip())
        combined = "\n".join(part for part in parts if part)
        return combined[:500] if combined else None


def build_vector_search_provider(
    app_settings: Settings = settings,
    client: OpenAI | None = None,
) -> VectorSearchProvider:
    if not app_settings.vector_search_configured:
        return DisabledVectorSearchProvider()
    return OpenAIVectorSearchProvider(app_settings, client)
