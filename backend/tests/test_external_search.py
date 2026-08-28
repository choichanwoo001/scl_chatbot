from __future__ import annotations

from types import SimpleNamespace

from app.config import Settings
from app.external_search import (
    ExternalSearchPolicy,
    OpenAIWebSearchProvider,
    safe_external_citation_url,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openai_api_key": "test-key",
        "external_web_search_enabled": True,
        "external_web_search_allowed_domains": (
            "scllab.co.kr",
            "kdca.go.kr",
            "pubmed.ncbi.nlm.nih.gov",
        ),
    }
    values.update(overrides)
    return Settings(**values)


def _response(url: str = "https://www.scllab.co.kr/front/check") -> SimpleNamespace:
    return SimpleNamespace(
        output_text="검색된 공식 페이지에서 확인한 내용입니다.",
        output=[
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "검색된 공식 페이지에서 확인한 내용입니다.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": url,
                                "title": "공식 페이지",
                            }
                        ],
                    }
                ],
            }
        ],
    )


def test_scl_operational_query_is_restricted_to_scl_domain() -> None:
    scope = ExternalSearchPolicy(_settings()).scope_for("SCL 검사 용기 알려줘", "test")

    assert scope is not None
    assert scope.allowed_domains == ("scllab.co.kr",)
    assert scope.source_tier == "scl_live_web"


def test_general_test_background_uses_only_approved_external_domains() -> None:
    scope = ExternalSearchPolicy(_settings()).scope_for("PCR 원리를 설명해줘", "test")

    assert scope is not None
    assert "scllab.co.kr" not in scope.allowed_domains
    assert scope.source_tier == "approved_external"


def test_external_url_validation_rejects_unapproved_and_lookalike_hosts() -> None:
    allowed = ("scllab.co.kr",)

    assert safe_external_citation_url("https://www.scllab.co.kr/front", allowed)
    assert safe_external_citation_url("https://scllab.co.kr.evil.example/front", allowed) is None
    assert safe_external_citation_url("http://www.scllab.co.kr/front", allowed) is None


def test_web_search_reply_requires_a_valid_citation() -> None:
    provider = OpenAIWebSearchProvider(_settings())
    captured: dict[str, object] = {}

    def create(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _response()

    provider.client = SimpleNamespace(responses=SimpleNamespace(create=create))
    reply = provider.search("SCL 검사 용기 알려줘", "test")

    assert reply is not None
    assert reply.data_status == "scl_live_web"
    assert reply.grounding_status == "grounded_external"
    assert reply.citations[0].source_tier == "scl_live_web"
    assert captured["tools"][0]["filters"]["allowed_domains"] == ["scllab.co.kr"]


def test_web_search_discards_a_disallowed_citation() -> None:
    provider = OpenAIWebSearchProvider(_settings())
    provider.client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **_: _response("https://untrusted.example/claim")
        )
    )

    assert provider.search("SCL 검사 용기 알려줘", "test") is None


def test_web_search_discards_partially_cited_claims() -> None:
    provider = OpenAIWebSearchProvider(_settings())
    response = _response()
    response.output_text = (
        "첫 번째 검사 사실은 공식 페이지에 있습니다. "
        "두 번째 검사 사실도 있다고 주장합니다."
    )
    response.output[0]["content"][0]["text"] = response.output_text
    provider.client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **_: response)
    )

    assert provider.search("SCL 검사 용기 알려줘", "test") is None


def test_external_reply_uses_allowlist_wording_and_keeps_source_link() -> None:
    provider = OpenAIWebSearchProvider(_settings())
    provider.client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **_: _response("https://pubmed.ncbi.nlm.nih.gov/12345")
        )
    )

    reply = provider.search("PCR 원리를 설명해줘", "test")

    assert reply is not None
    assert "승인된" not in reply.text
    assert "근거자료 링크" in reply.text
    assert str(reply.citations[0].url) == "https://pubmed.ncbi.nlm.nih.gov/12345"
