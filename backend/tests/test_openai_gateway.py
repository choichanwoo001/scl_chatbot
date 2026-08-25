from types import SimpleNamespace

import pytest
from app.config import Settings
from app.openai_gateway import ModelPlan, ModeratedContent, OpenAIGateway
from pydantic import ValidationError


def test_model_plan_accepts_public_navigation_support_intents() -> None:
    for sub_intent in ["get_location_or_contact", "navigate_site_route"]:
        plan = ModelPlan(
            domain="support",
            sub_intent=sub_intent,
            requested_action="navigate" if sub_intent == "navigate_site_route" else "explain",
            answer="안내합니다.",
        )

        assert plan.sub_intent == sub_intent


def _plan() -> ModelPlan:
    return ModelPlan(
        domain="test",
        sub_intent="get_test_detail",
        requested_action="explain",
        answer="검사 정보를 안내합니다.",
        matched_test_code="DEMO-C5621",
        confidence=0.95,
    )


class FakeResponses:
    def __init__(self, parsed: ModelPlan | None) -> None:
        self.parsed = parsed
        self.request: dict[str, object] | None = None

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.request = kwargs
        return SimpleNamespace(output_parsed=self.parsed, id="resp_test_123")


class FlakyStructuredResponses(FakeResponses):
    def __init__(self, parsed: ModelPlan) -> None:
        super().__init__(parsed)
        self.calls = 0
        self.requests: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls += 1
        self.requests.append(kwargs)
        if self.calls == 1:
            ModelPlan(
                domain="document",
                sub_intent="request_result_document",
                requested_action="download",
                answer="잘못된 조합",
            )
        return SimpleNamespace(output_parsed=self.parsed, id="resp_retry_ok")


class FlaggedStructuredResponses(FakeResponses):
    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.request = kwargs
        return SimpleNamespace(
            output_parsed=self.parsed,
            id="resp_flagged",
            moderation=SimpleNamespace(
                input=SimpleNamespace(flagged=True),
                output=SimpleNamespace(flagged=False),
            ),
        )


class FakeModerations:
    def __init__(self, flagged: bool) -> None:
        self.flagged = flagged
        self.request: dict[str, object] | None = None

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.request = kwargs
        return SimpleNamespace(results=[SimpleNamespace(flagged=self.flagged)])


class FakeClient:
    def __init__(self, parsed: ModelPlan | None, flagged: bool = False) -> None:
        self.responses = FakeResponses(parsed)
        self.moderations = FakeModerations(flagged)


def _gateway(parsed: ModelPlan | None = None, *, vector_store_id: str | None = None) -> OpenAIGateway:
    gateway = OpenAIGateway(
        Settings(
            openai_api_key="test-key",
            openai_vector_store_id=vector_store_id,
        )
    )
    gateway.client = FakeClient(parsed)
    return gateway


def test_responses_request_uses_structured_output_without_storage_or_file_search() -> None:
    gateway = _gateway(_plan())

    plan, response_id = gateway.plan("HPV 검사 용기 알려줘", [])

    request = gateway.client.responses.request
    assert response_id == "resp_test_123"
    assert plan.matched_test_code == "DEMO-C5621"
    assert request is not None
    assert request["text_format"] is ModelPlan
    assert request["store"] is False
    assert request["text"] == {"verbosity": "low"}
    assert request["reasoning"] == {"effort": "low"}
    assert request["moderation"] == {
        "model": "omni-moderation-latest",
        "policy": {"input": {"mode": "score"}, "output": {"mode": "score"}},
    }
    assert "tools" not in request
    assert request["input"][-1] == {"role": "user", "content": "HPV 검사 용기 알려줘"}


def test_model_managed_file_search_is_not_added_when_server_controls_vector_search() -> None:
    gateway = _gateway(_plan(), vector_store_id="vs_test")

    gateway.plan("검사 공문", [])

    request = gateway.client.responses.request
    assert request is not None
    assert "tools" not in request


def test_missing_structured_result_raises_for_orchestrator_fallback() -> None:
    gateway = _gateway(None)

    with pytest.raises(RuntimeError, match="structured result"):
        gateway.plan("검사 알려줘", [])


def test_integrated_moderation_blocks_a_structured_result() -> None:
    gateway = _gateway(_plan())
    gateway.client.responses = FlaggedStructuredResponses(_plan())

    with pytest.raises(ModeratedContent):
        gateway.plan("차단 대상 입력", [])


@pytest.mark.parametrize("flagged", [False, True])
def test_moderation_result_is_propagated(flagged: bool) -> None:
    gateway = _gateway(_plan())
    gateway.client.moderations.flagged = flagged

    assert gateway.moderate("공개 질문") is flagged
    assert gateway.client.moderations.request == {
        "model": "omni-moderation-latest",
        "input": "공개 질문",
    }


def test_domain_and_sub_intent_must_belong_to_the_same_branch() -> None:
    with pytest.raises(ValidationError, match="does not belong"):
        ModelPlan(
            domain="document",
            sub_intent="get_test_detail",
            requested_action="search",
            answer="잘못된 조합",
        )


def test_structured_validation_error_is_retried_once_with_branch_correction() -> None:
    gateway = _gateway(_plan())
    flaky = FlakyStructuredResponses(_plan())
    gateway.client.responses = flaky

    plan, response_id = gateway.plan("일반 검사의뢰서 다운로드", [])

    assert plan == _plan()
    assert response_id == "resp_retry_ok"
    assert flaky.calls == 2
    assert "domain과 sub_intent" in flaky.requests[1]["input"][-2]["content"]
