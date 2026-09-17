import base64
import json

import httpx
from app.config import Settings
from app.vector_search import (
    DisabledVectorSearchProvider,
    GeminiVectorSearchProvider,
    build_vector_search_provider,
)


def test_disabled_provider_is_used_without_complete_configuration(tmp_path):
    settings = Settings(
        gemini_api_key=None,
        gemini_vector_index_path=str(tmp_path / "missing.json"),
        vector_search_enabled=True,
    )

    provider = build_vector_search_provider(settings)

    assert isinstance(provider, DisabledVectorSearchProvider)
    assert provider.search("연휴 일정") == []


def test_gemini_vector_search_uses_local_mapping_and_google_embedding(tmp_path):
    path = tmp_path / "gemini-index.json"
    path.write_text(
        json.dumps(
            {
                "model": "gemini-embedding-2",
                "dimensions": 2,
                "items": [
                    {
                        "ref": "document:123",
                        "entity_type": "document",
                        "entity_id": "123",
                        "title": "공휴일 안내",
                        "snippet": "공휴일에는 검체 접수 시간이 단축됩니다.",
                        "vector": base64.b64encode(bytes([127, 0])).decode(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        gemini_api_key="gemini-test",
        gemini_embedding_dimensions=2,
        gemini_vector_index_path=str(path),
        vector_search_enabled=True,
        vector_search_min_score=0.25,
    )

    def respond(request):
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.headers["x-goog-api-key"] == "gemini-test"
        return httpx.Response(200, json={"embedding": {"values": [1.0, 0.0]}})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = GeminiVectorSearchProvider(settings, client)
    try:
        hits = provider.search("연휴 때 접수 가능한가요?", {"document"}, 5)
    finally:
        client.close()

    assert [hit.ref for hit in hits] == ["document:123"]
    assert hits[0].snippet == "공휴일에는 검체 접수 시간이 단축됩니다."
