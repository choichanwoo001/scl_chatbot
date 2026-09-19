import httpx
import pytest
from app.config import Settings
from app.result_provider import (
    HTTPResultProvider,
    ProviderAuthToken,
    ResultAuthenticationFailed,
    ResultProviderProtocolError,
    ResultProviderUnavailable,
    ResultService,
)
from app.schemas import ResultCredentials


def test_unconfigured_provider_fails_without_retaining_credentials() -> None:
    service = ResultService(Settings(result_provider_mode="unconfigured"))
    payload = ResultCredentials(
        session_id="result-session",
        user_id="private-id",
        password="private-password",
        identity_value="900101",
    )

    with pytest.raises(ResultProviderUnavailable):
        service.authenticate(payload)

    assert service._contexts == {}


def test_http_provider_authenticates_pages_and_maps_detail() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/auth/sessions":
            assert request.headers["x-client-id"] == "client"
            return httpx.Response(200, json={"access_token": "opaque-token", "expires_in": 120})
        assert request.headers["authorization"] == "Bearer opaque-token"
        if request.url.path == "/v1/results" and "cursor" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "result_id": "R-1",
                            "test_name": "HPV",
                            "requested_at": "2026-08-20",
                            "status": "reported",
                        }
                    ],
                    "next_cursor": "page-2",
                },
            )
        if request.url.path == "/v1/results":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if request.url.path == "/v1/results/R-1":
            return httpx.Response(
                200,
                json={
                    "result_id": "R-1",
                    "test_name": "HPV",
                    "requested_at": "2026-08-20",
                    "reported_at": "2026-08-21",
                    "status": "reported",
                    "fields": [{"name": "결과", "value": "보고 완료", "reference": None}],
                    "notice": "의료적 해석은 담당 의료진과 상담하세요.",
                },
            )
        return httpx.Response(204)

    settings = Settings(
        result_provider_mode="http",
        result_api_base_url="https://results.example.test",
        result_api_client_id="client",
        result_api_client_secret="secret",
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = HTTPResultProvider(settings, client)

    authenticated = provider.authenticate("user", "password", "identity")
    assert authenticated == ProviderAuthToken("opaque-token", 120)
    assert provider.list_results(authenticated.value)[0].result_id == "R-1"
    assert provider.get_result(authenticated.value, "R-1").fields[0].value == "보고 완료"
    provider.logout(authenticated.value)
    assert requests[-1].url.path == "/v1/auth/logout"


def test_http_provider_maps_authentication_and_protocol_errors() -> None:
    settings = Settings(result_provider_mode="http", result_api_base_url="https://results.example.test")
    unauthorized = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"error": "invalid_credentials"})
        )
    )
    with pytest.raises(ResultAuthenticationFailed):
        HTTPResultProvider(settings, unauthorized).authenticate("user", "bad", "identity")

    malformed = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"token": "wrong-field"}))
    )
    with pytest.raises(ResultProviderProtocolError):
        HTTPResultProvider(settings, malformed).authenticate("user", "password", "identity")


def test_http_provider_rejects_plain_http_by_default() -> None:
    with pytest.raises(ResultProviderUnavailable):
        HTTPResultProvider(Settings(result_api_base_url="http://results.example.test"))


def test_result_service_rejects_unknown_provider_mode() -> None:
    with pytest.raises(ResultProviderUnavailable):
        ResultService(Settings(result_provider_mode="typo"))
