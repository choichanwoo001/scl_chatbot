import json

from app.config import Settings
from app.vector_index import vector_index_status


def test_gemini_vector_index_status_reads_local_snapshot(tmp_path):
    path = tmp_path / "gemini-index.json"
    path.write_text(
        json.dumps(
            {
                "created_at": "2026-09-17T00:00:00Z",
                "items": [
                    {"entity_type": "document"},
                    {"entity_type": "faq"},
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        gemini_api_key="test-key",
        gemini_vector_index_path=str(path),
        vector_search_enabled=True,
    )

    status = vector_index_status(settings)

    assert status["configured"] is True
    assert status["counts"] == {"completed": 2}
    assert status["completed_by_type"] == {"document": 1, "faq": 1}


def test_missing_gemini_vector_index_is_unconfigured(tmp_path):
    status = vector_index_status(
        Settings(gemini_api_key="test-key", gemini_vector_index_path=str(tmp_path / "missing.json"))
    )

    assert status["configured"] is False
    assert status["counts"] == {}
