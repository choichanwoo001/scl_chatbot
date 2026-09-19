from __future__ import annotations

import re
import ssl
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlparse

import httpx
from pydantic import ValidationError

from .config import Settings
from .schemas import ResultCredentials, ResultDetail, ResultListItem


class ResultProviderUnavailable(RuntimeError):
    pass


class ResultAuthenticationFailed(RuntimeError):
    pass


class ResultSessionExpired(RuntimeError):
    pass


class ResultProviderProtocolError(ResultProviderUnavailable):
    pass


@dataclass(frozen=True)
class ProviderAuthToken:
    value: str
    expires_in_seconds: int | None = None


class ResultProvider(Protocol):
    name: str

    def authenticate(self, user_id: str, password: str, identity_value: str) -> str | ProviderAuthToken: ...
    def list_results(self, provider_token: str) -> list[ResultListItem]: ...
    def get_result(self, provider_token: str, result_id: str) -> ResultDetail: ...
    def logout(self, provider_token: str) -> None: ...


class UnconfiguredResultProvider:
    name = "unconfigured"

    def authenticate(self, user_id: str, password: str, identity_value: str) -> str:
        raise ResultProviderUnavailable("SCL 개인 검사결과 API 연동 정보가 아직 설정되지 않았습니다.")

    def list_results(self, provider_token: str) -> list[ResultListItem]:
        raise ResultProviderUnavailable("SCL 개인 검사결과 API 연동 정보가 아직 설정되지 않았습니다.")

    def get_result(self, provider_token: str, result_id: str) -> ResultDetail:
        raise ResultProviderUnavailable("SCL 개인 검사결과 API 연동 정보가 아직 설정되지 않았습니다.")

    def logout(self, provider_token: str) -> None:
        return None


class HTTPResultProvider:
    """Adapter for docs/appendices/technical/result-provider-openapi.yaml."""

    name = "http"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        base_url = (settings.result_api_base_url or "").strip().rstrip("/")
        parsed = urlparse(base_url)
        if not base_url or parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ResultProviderUnavailable("RESULT_API_BASE_URL이 올바르게 설정되지 않았습니다.")
        if parsed.scheme != "https" and not settings.result_api_allow_http:
            raise ResultProviderUnavailable("개인 검사결과 API는 HTTPS 주소만 사용할 수 있습니다.")
        if parsed.username or parsed.password:
            raise ResultProviderUnavailable("개인 검사결과 API URL에 인증정보를 포함할 수 없습니다.")
        self.base_url = base_url
        for path in (
            settings.result_api_auth_path,
            settings.result_api_list_path,
            settings.result_api_detail_path,
            settings.result_api_logout_path,
        ):
            self._validate_path(path)
        self._owns_client = client is None
        self.client = client or self._build_client(settings)

    def authenticate(self, user_id: str, password: str, identity_value: str) -> ProviderAuthToken:
        response = self._request(
            "POST",
            self.settings.result_api_auth_path,
            json={"user_id": user_id, "password": password, "identity_value": identity_value},
            authentication_request=True,
        )
        data = self._json_object(response)
        token = data.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise ResultProviderProtocolError("결과조회 인증 응답에 access_token이 없습니다.")
        expires_in = data.get("expires_in")
        if expires_in is not None and (not isinstance(expires_in, int) or expires_in <= 0):
            raise ResultProviderProtocolError("결과조회 인증 응답의 expires_in이 올바르지 않습니다.")
        return ProviderAuthToken(token.strip(), expires_in)

    def list_results(self, provider_token: str) -> list[ResultListItem]:
        items: list[ResultListItem] = []
        cursor: str | None = None
        for _ in range(20):
            response = self._request(
                "GET",
                self.settings.result_api_list_path,
                token=provider_token,
                params={"cursor": cursor} if cursor else None,
            )
            data = self._json_object(response)
            raw_items = data.get("items")
            if not isinstance(raw_items, list):
                raise ResultProviderProtocolError("결과 목록 응답에 items 배열이 없습니다.")
            try:
                items.extend(ResultListItem.model_validate(item) for item in raw_items)
            except ValidationError as error:
                raise ResultProviderProtocolError("결과 목록 응답 형식이 계약과 다릅니다.") from error
            next_cursor = data.get("next_cursor")
            if next_cursor is None:
                return items
            if not isinstance(next_cursor, str) or not next_cursor:
                raise ResultProviderProtocolError("결과 목록의 next_cursor가 올바르지 않습니다.")
            cursor = next_cursor
        raise ResultProviderProtocolError("결과 목록 페이지 수가 안전 제한을 초과했습니다.")

    def get_result(self, provider_token: str, result_id: str) -> ResultDetail:
        if not result_id or len(result_id) > 200:
            raise KeyError(result_id)
        path = self.settings.result_api_detail_path.replace("{result_id}", quote(result_id, safe=""))
        response = self._request("GET", path, token=provider_token)
        try:
            return ResultDetail.model_validate(self._json_object(response))
        except ValidationError as error:
            raise ResultProviderProtocolError("결과 상세 응답 형식이 계약과 다릅니다.") from error

    def logout(self, provider_token: str) -> None:
        try:
            self._request("POST", self.settings.result_api_logout_path, token=provider_token)
        except ResultProviderUnavailable:
            # Local authentication is cleared even if the upstream logout endpoint is unavailable.
            return None

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        authentication_request: bool = False,
    ) -> httpx.Response:
        self._validate_path(path)
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.settings.result_api_client_id:
            headers["X-Client-Id"] = self.settings.result_api_client_id
        if self.settings.result_api_client_secret:
            headers["X-Client-Secret"] = self.settings.result_api_client_secret
        try:
            response = self.client.request(
                method, f"{self.base_url}{path}", headers=headers, json=json, params=params
            )
        except httpx.HTTPError as error:
            raise ResultProviderUnavailable("개인 검사결과 시스템에 연결할 수 없습니다.") from error
        if response.status_code in {401, 403}:
            if authentication_request:
                raise ResultAuthenticationFailed("아이디, 비밀번호 또는 본인확인 정보를 확인해 주세요.")
            raise ResultSessionExpired("결과조회 인증이 만료되었습니다. 다시 인증해 주세요.")
        if response.status_code == 404 and not authentication_request:
            raise KeyError(path)
        if response.status_code == 429:
            raise ResultProviderUnavailable("개인 검사결과 시스템 요청이 일시적으로 제한되었습니다.")
        if response.status_code >= 400:
            raise ResultProviderUnavailable(
                f"개인 검사결과 시스템 오류가 발생했습니다(HTTP {response.status_code})."
            )
        return response

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, object]:
        try:
            data = response.json()
        except ValueError as error:
            raise ResultProviderProtocolError("결과조회 시스템이 JSON이 아닌 응답을 반환했습니다.") from error
        if not isinstance(data, dict):
            raise ResultProviderProtocolError("결과조회 시스템 응답이 JSON 객체가 아닙니다.")
        return data

    @staticmethod
    def _validate_path(path: str) -> None:
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise ResultProviderUnavailable("결과조회 API 경로 설정이 올바르지 않습니다.")

    @staticmethod
    def _build_client(settings: Settings) -> httpx.Client:
        context = ssl.create_default_context(cafile=settings.result_api_ca_bundle or None)
        if settings.result_api_client_cert:
            cert_path = Path(settings.result_api_client_cert)
            key_path = Path(settings.result_api_client_key) if settings.result_api_client_key else None
            if not cert_path.is_file() or (key_path and not key_path.is_file()):
                raise ResultProviderUnavailable("결과조회 mTLS 인증서 경로가 올바르지 않습니다.")
            context.load_cert_chain(str(cert_path), str(key_path) if key_path else None)
        return httpx.Client(
            timeout=settings.result_api_timeout_seconds,
            verify=context,
            follow_redirects=False,
        )


@dataclass
class ResultAuthContext:
    provider_token: str
    expires_at: datetime


class ResultService:
    def __init__(self, settings: Settings, provider: ResultProvider | None = None) -> None:
        if provider is not None:
            self.provider = provider
        elif settings.result_provider_mode == "http":
            self.provider = HTTPResultProvider(settings)
        elif settings.result_provider_mode == "unconfigured":
            self.provider = UnconfiguredResultProvider()
        else:
            raise ResultProviderUnavailable(
                f"지원하지 않는 RESULT_PROVIDER_MODE입니다: {settings.result_provider_mode}"
            )
        self.settings = settings
        self._contexts: dict[str, ResultAuthContext] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id):
            raise ResultAuthenticationFailed("유효하지 않은 채팅 세션입니다.")

    def authenticate(self, payload: ResultCredentials) -> ResultAuthContext:
        self._validate_session_id(payload.session_id)
        # Credentials are consumed in this call and are never retained.
        authenticated = self.provider.authenticate(payload.user_id, payload.password, payload.identity_value)
        if isinstance(authenticated, ProviderAuthToken):
            token = authenticated.value
            provider_ttl = authenticated.expires_in_seconds
        else:
            token = authenticated
            provider_ttl = None
        configured_ttl = max(1, self.settings.result_session_ttl_minutes * 60)
        ttl_seconds = min(configured_ttl, provider_ttl) if provider_ttl else configured_ttl
        context = ResultAuthContext(token, datetime.now(UTC) + timedelta(seconds=ttl_seconds))
        with self._lock:
            self._contexts[payload.session_id] = context
        return context

    def list_results(self, session_id: str) -> list[ResultListItem]:
        return self.provider.list_results(self._context(session_id).provider_token)

    def get_result(self, session_id: str, result_id: str) -> ResultDetail:
        return self.provider.get_result(self._context(session_id).provider_token, result_id)

    def clear(self, session_id: str) -> None:
        with self._lock:
            context = self._contexts.pop(session_id, None)
        if context:
            self.provider.logout(context.provider_token)

    def _context(self, session_id: str) -> ResultAuthContext:
        self._validate_session_id(session_id)
        with self._lock:
            context = self._contexts.get(session_id)
            if context and context.expires_at <= datetime.now(UTC):
                self._contexts.pop(session_id, None)
                expired = context
                context = None
            else:
                expired = None
        if expired:
            self.provider.logout(expired.provider_token)
        if not context:
            raise ResultSessionExpired("결과조회 인증이 만료되었습니다. 다시 인증해 주세요.")
        return context
