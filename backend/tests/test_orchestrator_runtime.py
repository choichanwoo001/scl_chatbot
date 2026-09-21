import asyncio

from app.chat_contracts import ModelPlan, ModeratedContent
from app.chat_retrieval import retrieve_context
from app.config import Settings
from app.database import SessionLocal
from app.models import DataSource, PublicDocument
from app.normalization import normalize_search_text
from app.orchestrator import ChatOrchestrator, SessionStore
from app.public_search import SearchHit


class FakeGateway:
    def __init__(
        self, *, flagged: bool = False, plan: ModelPlan | None = None, error: Exception | None = None
    ) -> None:
        self.flagged = flagged
        self.result = plan
        self.error = error
        self.plan_calls: list[tuple[str, list[dict[str, str]]]] = []

    def interpret(self, message, history, previous):
        if self.flagged:
            raise ModeratedContent("blocked")
        if self.error:
            raise self.error
        return None

    def retrieve_interpreted(self, message, interpreted, previous):
        return retrieve_context(message, orchestrator_catalog, orchestrator_search)

    def plan(self, message: str, history: list[dict[str, str]], retrieval) -> tuple[ModelPlan, str]:
        self.plan_calls.append((message, history.copy()))
        assert self.result is not None
        return self.result, "resp_fake"


class SequenceGateway(FakeGateway):
    def __init__(self, plans: list[ModelPlan]) -> None:
        super().__init__()
        self.plans = plans

    def plan(self, message: str, history: list[dict[str, str]], retrieval) -> tuple[ModelPlan, str]:
        self.plan_calls.append((message, history.copy()))
        return self.plans.pop(0), "resp_sequence"


def _orchestrator() -> ChatOrchestrator:
    runtime = ChatOrchestrator(Settings())
    global orchestrator_catalog, orchestrator_search
    orchestrator_catalog = runtime.catalog
    orchestrator_search = runtime.public_search
    return runtime


def test_moderation_block_prevents_model_planning() -> None:
    orchestrator = _orchestrator()
    gateway = FakeGateway(flagged=True)
    orchestrator.gateway = gateway

    response = asyncio.run(orchestrator.respond("위험한 요청", None))

    assert response.domain == "safety"
    assert response.safety_action == "block"
    assert gateway.plan_calls == []


def test_gateway_error_falls_back_to_local_search() -> None:
    orchestrator = _orchestrator()
    orchestrator.gateway = FakeGateway(error=TimeoutError("timeout"))

    response = asyncio.run(orchestrator.respond("HPV 검사 용기 알려줘", None))

    assert response.mode == "demo_fallback"
    assert response.reply.kind == "test"
    assert response.reply.test is not None
    assert response.reply.test.code == "DEMO-C5621"
    assert response.reply.text.startswith("현재 AI 연결이 지연되어")


def test_structured_plan_flags_are_exposed_in_api_response() -> None:
    orchestrator = _orchestrator()
    orchestrator.gateway = FakeGateway(
        plan=ModelPlan(
            domain="result",
            sub_intent="interpret_personal_result",
            requested_action="explain",
            answer="개인 결과는 의료진에게 확인해 주세요.",
            requires_authentication=True,
            needs_handoff=True,
            medical_review_required=True,
            confidence=0.98,
        )
    )

    response = asyncio.run(orchestrator.respond("내 검사결과 해석해줘", None))

    assert response.domain == "result"
    assert response.sub_intent == "interpret_personal_result"
    assert response.requires_authentication is True
    assert response.needs_handoff is True
    assert response.medical_review_required is True
    assert response.safety_action == "handoff"


def test_result_interpretation_enforces_auth_and_medical_handoff_flags() -> None:
    orchestrator = _orchestrator()
    orchestrator.gateway = FakeGateway(
        plan=ModelPlan(
            domain="result",
            sub_intent="interpret_personal_result",
            requested_action="explain",
            answer="개인 결과는 의료진에게 확인해 주세요.",
        )
    )

    response = asyncio.run(orchestrator.respond("내 검사결과를 진단해줘", None))

    assert response.reply.kind == "result_auth_form"
    assert response.requires_authentication is True
    assert response.needs_handoff is True
    assert response.medical_review_required is True
    assert response.safety_action == "handoff"


def test_result_normality_question_uses_medical_handoff_without_model() -> None:
    orchestrator = _orchestrator()
    gateway = FakeGateway(error=AssertionError("model must not run"))
    orchestrator.gateway = gateway

    response = asyncio.run(orchestrator.respond("검사결과 수치가 정상인지 해석해줘", None))

    assert response.reply.kind == "handoff_form"
    assert "임의로 판단할 수 없습니다" in response.reply.text
    assert response.medical_review_required is True
    assert response.safety_action == "handoff"


def test_high_confidence_navigation_and_location_queries_skip_model(monkeypatch) -> None:
    orchestrator = _orchestrator()
    gateway = FakeGateway(error=AssertionError("model must not run"))
    orchestrator.gateway = gateway
    route = SearchHit(
        ref="route:1",
        entity_type="route",
        entity_id="1",
        title="의뢰방법",
        snippet="/front/check/howto_request.do",
        source_url="https://www.scllab.co.kr/front/check/howto_request.do",
        updated_at="2026-08-20",
        score=100,
    )
    location = SearchHit(
        ref="location:1",
        entity_type="location",
        entity_id="1",
        title="대구",
        snippet="대구광역시 테스트로 1 · 053-000-0000",
        source_url="https://www.scllab.co.kr/front/about/network_list.do",
        updated_at="2026-08-20",
        score=100,
    )

    def search(query, types, limit):
        return [route] if "route" in types else [location]

    monkeypatch.setattr(orchestrator.public_search, "search", search)
    route_response = asyncio.run(orchestrator.respond("검사 의뢰 방법이 어디에 나와 있어?", None))
    location_response = asyncio.run(
        orchestrator.respond("대구 SCL 지점 주소와 전화번호 알려줘", None)
    )

    assert route_response.reply.citations[0].ref == "route:1"
    assert location_response.reply.citations[0].ref == "location:1"
    assert "053-000-0000" in location_response.reply.text


def test_recent_new_test_notice_is_sorted_by_date(monkeypatch) -> None:
    orchestrator = _orchestrator()
    orchestrator.gateway = FakeGateway(error=AssertionError("model must not run"))
    older = SearchHit(
        ref="document:1",
        entity_type="document",
        entity_id="1",
        title="2017 신규검사 안내",
        snippet=None,
        source_url="https://www.scllab.co.kr/old",
        updated_at="2017-01-01",
        score=100,
    )
    newer = SearchHit(
        ref="document:2",
        entity_type="document",
        entity_id="2",
        title="2026 신규검사 안내",
        snippet=None,
        source_url="https://www.scllab.co.kr/new",
        updated_at="2026-08-01",
        score=50,
    )
    monkeypatch.setattr(
        orchestrator.public_search,
        "recent_documents",
        lambda *args, **kwargs: [newer, older],
    )

    response = asyncio.run(orchestrator.respond("최근 신규검사 안내 공문 찾아줘", None))

    assert response.reply.citations[0].title == "2026 신규검사 안내"


def test_document_plan_without_model_ref_uses_matching_public_document() -> None:
    with SessionLocal.begin() as session:
        source = DataSource(
            key="DOC_FALLBACK_TEST",
            name="document fallback test",
            base_url="https://www.scllab.co.kr",
        )
        session.add(source)
        session.flush()
        session.add(
            PublicDocument(
                data_source_id=source.id,
                source_external_key="breast-ihc-doc",
                document_type="official_notice",
                title="Breast invasive carcinoma 면역조직화학염색 안내",
                normalized_title=normalize_search_text("Breast invasive carcinoma 면역조직화학염색 안내"),
                body_text="관련 검사 안내 문서입니다.",
                source_url="https://www.scllab.co.kr/front/document",
                content_hash="d" * 64,
            )
        )
    orchestrator = _orchestrator()
    orchestrator.gateway = FakeGateway(
        plan=ModelPlan(
            domain="document",
            sub_intent="search_general_notice",
            requested_action="search",
            answer="문서를 찾지 못했습니다.",
        )
    )

    response = asyncio.run(
        orchestrator.respond(
            "Breast invasive carcinoma 면역조직화학염색 문서 찾아줘",
            None,
        )
    )

    assert response.reply.data_status == "public_document"
    assert response.reply.citations
    assert "관련 공개 문서를 찾았습니다" in response.reply.text


def test_session_history_is_bounded_and_invalid_id_is_replaced() -> None:
    store = SessionStore(history_limit=4)
    safe_id, state = store.get("../../invalid")
    assert safe_id != "../../invalid"

    for index in range(4):
        store.append(state, f"질문 {index}", f"답변 {index}")

    assert len(state.history) == 4
    assert state.history[0]["content"] == "질문 2"
    assert state.history[-1]["content"] == "답변 3"


def test_previous_turn_is_passed_to_the_next_structured_call() -> None:
    orchestrator = _orchestrator()
    gateway = FakeGateway(
        plan=ModelPlan(
            domain="test",
            sub_intent="get_test_detail",
            requested_action="explain",
            answer="HPV 검사 정보입니다.",
            matched_test_code="DEMO-C5621",
            confidence=0.95,
        )
    )
    orchestrator.gateway = gateway

    first = asyncio.run(orchestrator.respond("HPV 검사 알려줘", None))
    asyncio.run(orchestrator.respond("그 검사 소요일은?", first.session_id))

    assert len(gateway.plan_calls) == 2
    second_history = gateway.plan_calls[1][1]
    assert second_history[-2]["content"] == "HPV 검사 알려줘"
    assert second_history[-1]["content"].startswith("확인된 SCL 공개 검사 항목")


def test_test_followup_reuses_the_exact_variant_from_session_state() -> None:
    orchestrator = _orchestrator()
    orchestrator.gateway = SequenceGateway(
        [
            ModelPlan(
                domain="test",
                sub_intent="get_test_detail",
                requested_action="explain",
                answer="HPV 검사 정보입니다.",
                matched_test_code="DEMO-C5621",
            ),
            ModelPlan(
                domain="test",
                sub_intent="get_test_detail",
                requested_action="explain",
                answer="앞선 검사 정보를 확인합니다.",
                matched_test_code="INVALID",
                needs_clarification=True,
                requires_authentication=True,
                needs_handoff=True,
                medical_review_required=True,
            ),
        ]
    )

    first = asyncio.run(orchestrator.respond("HPV 검사 알려줘", None))
    second = asyncio.run(orchestrator.respond("그 검사는 무슨 용기고 며칠 걸려?", first.session_id))

    assert second.reply.kind == "test"
    assert second.reply.test is not None
    assert second.reply.test.code == "DEMO-C5621"
    assert second.reply.citations
