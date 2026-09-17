from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import Settings, settings

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


class GeminiVectorSearchProvider:
    """Local cosine search over a Gemini-generated public-data index."""

    def __init__(self, app_settings: Settings = settings, client: httpx.Client | None = None) -> None:
        if not app_settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for vector search")
        path = Path(app_settings.gemini_vector_index_path)
        if not path.is_file():
            raise ValueError(f"Gemini vector index does not exist: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("model") != app_settings.gemini_embedding_model:
            raise ValueError("Gemini vector index model does not match configuration")
        if int(payload.get("dimensions") or 0) != app_settings.gemini_embedding_dimensions:
            raise ValueError("Gemini vector index dimensions do not match configuration")
        self.settings = app_settings
        self.items = list(payload.get("items") or [])
        self.client = client or httpx.Client(timeout=app_settings.vector_search_timeout_seconds)

    def search(
        self,
        query: str,
        types: set[str] | None = None,
        limit: int | None = None,
    ) -> list[VectorSearchHit]:
        selected_types = (set(types) if types else set(VECTOR_ENTITY_TYPES)) & VECTOR_ENTITY_TYPES
        if not query.strip() or not selected_types:
            return []
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.settings.gemini_embedding_model}:embedContent"
        )
        response = self.client.post(
            endpoint,
            headers={"x-goog-api-key": self.settings.gemini_api_key},
            json={
                "content": {"parts": [{"text": f"task: search result | query: {query.strip()}"}]},
                "outputDimensionality": self.settings.gemini_embedding_dimensions,
            },
        )
        response.raise_for_status()
        query_vector = response.json().get("embedding", {}).get("values") or []
        if len(query_vector) != self.settings.gemini_embedding_dimensions:
            raise RuntimeError("Gemini query embedding has an unexpected dimension")
        query_norm = math.sqrt(sum(float(value) ** 2 for value in query_vector)) or 1.0
        rows: list[tuple[float, dict[str, Any]]] = []
        for item in self.items:
            if item.get("entity_type") not in selected_types:
                continue
            raw = base64.b64decode(item["vector"])
            if len(raw) != len(query_vector):
                continue
            dot = 0.0
            vector_norm_sq = 0.0
            for byte, query_value in zip(raw, query_vector, strict=True):
                signed = byte if byte < 128 else byte - 256
                dot += signed * float(query_value)
                vector_norm_sq += signed * signed
            score = dot / ((math.sqrt(vector_norm_sq) or 1.0) * query_norm)
            if score >= self.settings.vector_search_min_score:
                rows.append((score, item))
        rows.sort(key=lambda row: (-row[0], str(row[1].get("title") or "")))
        max_results = max(1, min(limit or self.settings.vector_search_max_results, 50))
        return [
            VectorSearchHit(
                ref=str(item["ref"]),
                entity_type=str(item["entity_type"]),
                entity_id=str(item["entity_id"]),
                score=float(score),
                snippet=str(item.get("snippet") or "")[:500] or None,
                file_id=f"gemini:{item['ref']}",
                filename=str(item.get("title") or item["ref"]),
                attributes={"provider": "gemini", "embedding_model": self.settings.gemini_embedding_model},
            )
            for score, item in rows[:max_results]
        ]


def build_vector_search_provider(
    app_settings: Settings = settings,
    client: Any | None = None,
    *,
    session_factory=None,
) -> VectorSearchProvider:
    if not app_settings.vector_search_configured:
        return DisabledVectorSearchProvider()
    return GeminiVectorSearchProvider(app_settings, client)
