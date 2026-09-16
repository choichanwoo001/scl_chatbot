import asyncio

import httpx
import pytest
from app.config import Settings
from app.gemini_gateway import GeminiGateway
from app.orchestrator import ChatOrchestrator, LiveChatUnavailable


def test_missing_gemini_key_never_uses_existing_openai_key():
    settings = Settings(llm_provider="gemini", gemini_api_key=None, openai_api_key="legacy-key", external_web_search_enabled=True)
    runtime = ChatOrchestrator(settings)
    assert runtime.gateway is None
    assert runtime.external_search is None
    assert settings.mode == "demo_fallback"
    assert settings.chat_model == settings.gemini_model
    assert not settings.live_chat_available
    with pytest.raises(LiveChatUnavailable):
        asyncio.run(runtime.respond("HPV", None, require_live=True))


def test_gemini_request_uses_google_and_preserves_response_contract():
    settings = Settings(llm_provider="gemini", gemini_api_key="gemini-test", openai_api_key="legacy-key", vector_search_enabled=False, external_web_search_enabled=True)
    runtime = ChatOrchestrator(settings)
    assert isinstance(runtime.gateway, GeminiGateway)
    assert runtime.external_search is None
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.headers["x-goog-api-key"] == "gemini-test"
        return httpx.Response(503, json={"error": {"message": "unavailable"}})

    runtime.gateway.client.close()
    runtime.gateway.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(LiveChatUnavailable):
            asyncio.run(runtime.respond("HPV", None, require_live=True))
        assert len(requests) == 1
        assert isinstance(runtime.gateway, GeminiGateway)
    finally:
        runtime.gateway.client.close()
