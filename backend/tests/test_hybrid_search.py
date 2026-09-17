from dataclasses import replace

from app.config import Settings
from app.database import SessionLocal
from app.models import DataSource, PublicDocument
from app.normalization import normalize_search_text
from app.public_search import PublicDataSearch
from app.vector_search import VectorSearchHit


class FakeVectorProvider:
    def __init__(self, hits: list[VectorSearchHit] | None = None, error: Exception | None = None) -> None:
        self.hits = hits or []
        self.error = error
        self.calls = 0

    def search(self, query: str, types: set[str] | None = None, limit: int | None = None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.hits


def _seed_document() -> int:
    with SessionLocal.begin() as session:
        source = session.query(DataSource).filter_by(key="HYBRID_TEST").first()
        if source is None:
            source = DataSource(
                key="HYBRID_TEST", name="hybrid test", base_url="https://www.scllab.co.kr"
            )
            session.add(source)
            session.flush()
        item = session.query(PublicDocument).filter_by(source_external_key="hybrid-doc").first()
        if item is None:
            item = PublicDocument(
                data_source_id=source.id,
                source_external_key="hybrid-doc",
                document_type="notice",
                title="공휴일 검체 접수 운영 안내",
                normalized_title=normalize_search_text("공휴일 검체 접수 운영 안내"),
                body_text="법정 공휴일에는 검체 접수 시간이 단축됩니다.",
                source_url="https://www.scllab.co.kr/hybrid",
                content_hash="e" * 64,
            )
            session.add(item)
            session.flush()
        return item.id


def _vector_hit(document_id: int) -> VectorSearchHit:
    return VectorSearchHit(
        ref=f"document:{document_id}",
        entity_type="document",
        entity_id=str(document_id),
        score=0.92,
        snippet="공휴일에는 검체 접수 시간이 단축됩니다.",
        file_id="file_hybrid",
        filename=f"document-{document_id}.md",
    )


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "gemini_api_key": "test-key",
        "vector_search_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_semantic_only_vector_hit_is_resolved_through_local_database() -> None:
    document_id = _seed_document()
    provider = FakeVectorProvider([_vector_hit(document_id)])
    search = PublicDataSearch(_settings(), provider)

    hits = search.search("빨간날에도 샘플 받아주나요?", {"document"}, 3)

    assert hits and hits[0].ref == f"document:{document_id}"
    assert hits[0].metadata["retrieval"] == "vector"
    assert provider.calls == 1


def test_vector_failure_falls_back_to_lexical_results() -> None:
    document_id = _seed_document()
    provider = FakeVectorProvider(error=TimeoutError("vector timeout"))
    search = PublicDataSearch(_settings(), provider)

    hits = search.search("공휴일 검체 접수", {"document"}, 3)

    assert any(hit.ref == f"document:{document_id}" for hit in hits)
    assert search.vector_search_errors == 1
    assert search.last_vector_error == "vector timeout"


def test_shadow_mode_observes_vector_without_changing_ranking() -> None:
    document_id = _seed_document()
    provider = FakeVectorProvider([replace(_vector_hit(document_id), score=0.99)])
    lexical = PublicDataSearch(_settings(vector_search_enabled=False))
    shadow = PublicDataSearch(_settings(vector_search_shadow_mode=True), provider)

    expected = lexical.search("공휴일 검체 접수", {"document"}, 3)
    actual = shadow.search("공휴일 검체 접수", {"document"}, 3)

    assert [hit.ref for hit in actual] == [hit.ref for hit in expected]
    assert provider.calls == 1


def test_disabled_vector_search_does_not_call_provider() -> None:
    provider = FakeVectorProvider(error=AssertionError("provider must not be called"))
    search = PublicDataSearch(_settings(vector_search_enabled=False), provider)

    search.search("공휴일 검사 일정", {"document"}, 3)

    assert provider.calls == 0
    assert search.vector_search_calls == 0
