import app.main as main_module
from app.config import Settings
from app.main import app, orchestrator
from app.result_provider import MockResultProvider, ResultService
from fastapi.testclient import TestClient

client = TestClient(app)


def test_health_reports_backend_mode() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mode"] in {"openai", "gemini", "demo_fallback"}
    assert body["rag_enabled"] == body["vector_search_configured"]
    assert isinstance(body["vector_index_completed"], int)
    assert isinstance(body["vector_index_items_with_errors"], int)


def test_chat_returns_demo_test_without_api_key() -> None:
    response = client.post("/api/chat", json={"message": "HPV 검사 용기 알려줘", "require_live": False})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"]["kind"] == "test"
    assert body["reply"]["test"]["code"] == "DEMO-C5621"
    assert body["reply"]["data_status"] == "demo_data"


def test_chat_live_required_never_returns_prepared_fallback() -> None:
    response = client.post("/api/chat", json={"message": "HPV 검사 용기 알려줘"})

    assert response.status_code == 503
    assert "준비된 답변으로 대체하지 않았습니다" in response.json()["detail"]


def test_chat_keeps_context_with_session_id() -> None:
    first = client.post("/api/chat", json={"message": "HPV 검사 알려줘", "require_live": False}).json()
    followup = client.post(
        "/api/chat",
        json={"message": "그 검사 용기는 뭐야?", "session_id": first["session_id"], "require_live": False},
    ).json()
    assert followup["reply"]["kind"] == "test"
    assert followup["reply"]["test"]["code"] == "DEMO-C5621"


def test_chat_does_not_echo_raw_phone_number() -> None:
    response = client.post(
        "/api/chat", json={"message": "010-1234-5678로 검사결과 알려줘", "require_live": False}
    )
    body = response.json()
    assert "010-1234-5678" not in body["displayed_input"]
    assert body["safety_action"] == "redact"


def test_public_data_status_contract() -> None:
    response = client.get("/api/public-data/status")
    assert response.status_code == 200
    assert set(response.json()) == {
        "documents",
        "containers",
        "preservatives",
        "locations",
        "routes",
        "taxonomy_terms",
        "published_faqs",
        "extracted_attachments",
        "vector_search_enabled",
        "vector_search_configured",
        "vector_search_shadow_mode",
        "vector_search_calls",
        "vector_search_errors",
        "last_vector_error",
        "vector_index_counts",
        "vector_index_completed_by_type",
        "vector_index_usage_bytes",
        "vector_index_items_with_errors",
        "vector_index_last_synced_at",
    }


def test_unified_search_and_detail_contract() -> None:
    response = client.get("/api/search", params={"q": "HPV", "types": "test", "limit": 2})
    assert response.status_code == 200
    hits = response.json()
    assert hits and hits[0]["entity_type"] == "test"
    detail = client.get(f"/api/public-data/test/{hits[0]['entity_id']}")
    assert detail.status_code == 200
    assert detail.json()["ref"] == hits[0]["ref"]


def test_unified_search_rejects_unknown_type() -> None:
    response = client.get("/api/search", params={"q": "공지", "types": "unknown"})
    assert response.status_code == 422


def test_search_endpoints_reject_empty_or_oversized_queries() -> None:
    assert client.get("/api/search", params={"q": ""}).status_code == 422
    assert client.get("/api/tests/search", params={"q": ""}).status_code == 422
    assert client.get("/api/search", params={"q": "가" * 501}).status_code == 422


def test_search_endpoints_reject_out_of_range_limits() -> None:
    assert client.get("/api/search", params={"q": "HPV", "limit": 0}).status_code == 422
    assert client.get("/api/search", params={"q": "HPV", "limit": 51}).status_code == 422
    assert client.get("/api/tests/search", params={"q": "HPV", "limit": 0}).status_code == 422
    assert client.get("/api/taxonomy/1/tests", params={"limit": 201}).status_code == 422


def test_taxonomy_endpoint_rejects_non_positive_id() -> None:
    assert client.get("/api/taxonomy/0/tests").status_code == 422


def test_session_can_be_explicitly_ended() -> None:
    created = client.post("/api/chat", json={"message": "HPV 검사 알려줘", "require_live": False}).json()
    session_id = created["session_id"]
    assert session_id in orchestrator.sessions._sessions

    response = client.delete(f"/api/sessions/{session_id}")

    assert response.status_code == 204
    assert session_id not in orchestrator.sessions._sessions


def test_cors_allows_frontend_delete_preflight() -> None:
    response = client.options(
        "/api/sessions/browser-session",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "DELETE",
        },
    )

    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]


def test_demo_chat_returns_secure_result_form() -> None:
    response = client.post("/api/chat", json={"message": "내 검사결과 보여줘", "require_live": False})
    assert response.status_code == 200
    assert response.json()["reply"]["kind"] == "result_auth_form"


def test_handoff_can_be_submitted_without_leaving_chat() -> None:
    response = client.post(
        "/api/handoff",
        json={
            "session_id": "api-handoff-session",
            "inquiry_type": "general",
            "requester_name": "테스트 사용자",
            "phone": "010-1234-5678",
            "organization": "테스트의원",
            "content": "상담이 필요합니다.",
            "related_refs": [],
            "consent": True,
        },
    )
    assert response.status_code == 201
    receipt = response.json()
    assert receipt["status"] == "submitted"
    assert client.get(f"/api/handoff/{receipt['public_id']}").status_code == 200


def test_feedback_is_saved_and_returns_faq_candidate() -> None:
    response = client.post(
        "/api/feedback",
        json={
            "session_id": "api-feedback-session",
            "response_id": "resp-test",
            "rating": "helpful",
            "reason": None,
            "comment": None,
            "question": "HPV 검사 용기는 무엇인가요?",
            "answer": "전용 수송용기입니다.",
            "domain": "test",
            "sub_intent": "get_test_detail",
            "source_refs": ["test:DEMO-C5621"],
        },
    )
    assert response.status_code == 201
    assert response.json()["faq_candidate_id"] > 0


def test_mock_result_api_never_returns_credentials(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    service = ResultService(Settings(openai_api_key=None), MockResultProvider())
    monkeypatch.setattr(main_module.services, "result_service", service)
    credentials = {
        "session_id": "api-result-session",
        "user_id": "demo-user",
        "password": "demo-pass",
        "identity_value": "900101",
    }
    authenticated = client.post("/api/results/authenticate", json=credentials)
    assert authenticated.status_code == 200
    assert all(secret not in authenticated.text for secret in credentials.values())

    results = client.get("/api/results", params={"session_id": "api-result-session"})
    assert results.status_code == 200
    detail = client.get(
        f"/api/results/{results.json()[0]['result_id']}",
        params={"session_id": "api-result-session"},
    )
    assert detail.status_code == 200
    assert "시연 데이터" in detail.text

    ended = client.delete("/api/sessions/api-result-session")
    assert ended.status_code == 204
    assert service._contexts == {}
