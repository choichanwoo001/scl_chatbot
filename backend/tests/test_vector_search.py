from types import SimpleNamespace

from app.config import Settings
from app.database import SessionLocal, init_database
from app.models import VectorIndexItem
from app.vector_search import (
    DisabledVectorSearchProvider,
    OpenAIVectorSearchProvider,
    build_vector_search_provider,
)


class FakeVectorStores:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.request: tuple[str, dict[str, object]] | None = None

    def search(self, vector_store_id: str, **kwargs: object) -> SimpleNamespace:
        self.request = (vector_store_id, kwargs)
        return SimpleNamespace(data=self.rows)


class FakeClient:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.vector_stores = FakeVectorStores(rows)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openai_api_key": "test-key",
        "openai_vector_store_id": "vs_test",
        "vector_search_enabled": True,
        "vector_search_min_score": 0.25,
    }
    values.update(overrides)
    return Settings(**values)


def test_disabled_provider_is_used_without_complete_configuration() -> None:
    provider = build_vector_search_provider(_settings(vector_search_enabled=False))

    assert isinstance(provider, DisabledVectorSearchProvider)
    assert provider.search("연휴 일정") == []


def test_vector_results_require_completed_local_mapping() -> None:
    init_database()
    with SessionLocal.begin() as session:
        session.add(
            VectorIndexItem(
                local_ref="document:123",
                entity_type="document",
                entity_id="123",
                vector_store_id="vs_test",
                openai_file_id="file_mapped",
                file_name="document-123.md",
                content_hash="a" * 64,
                index_status="completed",
            )
        )
    rows = [
        SimpleNamespace(
            file_id="file_mapped",
            filename="document-123.md",
            score=0.91,
            attributes={"entity_type": "document"},
            content=[SimpleNamespace(text="연휴 기간 검사 일정이 변경됩니다.")],
        ),
        SimpleNamespace(
            file_id="file_unmapped",
            filename="unknown.md",
            score=0.99,
            attributes={"entity_type": "document"},
            content=[SimpleNamespace(text="신뢰할 수 없는 결과")],
        ),
    ]
    client = FakeClient(rows)
    provider = OpenAIVectorSearchProvider(_settings(), client=client)

    hits = provider.search("연휴 때 접수 가능한가요?", {"document"}, 5)

    assert [hit.ref for hit in hits] == ["document:123"]
    assert hits[0].snippet == "연휴 기간 검사 일정이 변경됩니다."
    assert client.vector_stores.request == (
        "vs_test",
        {
            "query": "연휴 때 접수 가능한가요?",
            "filters": {"type": "in", "key": "entity_type", "value": ["document"]},
            "max_num_results": 5,
            "rewrite_query": True,
        },
    )


def test_vector_results_below_score_threshold_are_dropped() -> None:
    rows = [
        SimpleNamespace(
            file_id="file_low",
            filename="low.md",
            score=0.2,
            attributes={},
            content=[SimpleNamespace(text="낮은 점수")],
        )
    ]
    provider = OpenAIVectorSearchProvider(_settings(vector_search_min_score=0.5), client=FakeClient(rows))

    assert provider.search("질문", {"document"}) == []
