from app.config import Settings
from app.database import SessionLocal
from app.models import DataSource, PublicDocument, ServiceLocation
from app.normalization import normalize_search_text
from app.openai_gateway import ModelCitation, ModelPlan
from app.orchestrator import ChatOrchestrator
from app.schemas import TestInfo as CatalogTestInfo


def make_orchestrator() -> ChatOrchestrator:
    return ChatOrchestrator(Settings(openai_api_key=None))


def test_resolves_a_matched_test_code_to_catalog_data() -> None:
    plan = ModelPlan(
        domain="test",
        sub_intent="get_test_detail",
        requested_action="explain",
        answer="HPV 검사 정보를 안내합니다.",
        matched_test_code="DEMO-C5621",
        requested_fields=["container", "tat"],
        confidence=0.98,
    )

    reply, matched = make_orchestrator()._reply_from_plan(plan)

    assert reply.kind == "test"
    assert matched is not None
    assert matched.code == "DEMO-C5621"


def test_matched_test_keeps_selected_public_document_citation() -> None:
    document_id, _ = _seed_routing_public_data()
    plan = ModelPlan(
        domain="test",
        sub_intent="request_preparation_guide",
        requested_action="explain",
        answer="공개 안내에 따른 준비사항입니다.",
        matched_test_code="DEMO-C5621",
        matched_content_id=f"document:{document_id}",
        confidence=0.98,
    )

    reply, matched = make_orchestrator()._reply_from_plan(plan)

    assert matched is not None
    assert reply.kind == "test"
    assert any(item.ref == f"document:{document_id}" for item in reply.citations)


def test_public_detail_supporting_test_becomes_a_verified_citation() -> None:
    test = CatalogTestInfo(
        code="30130",
        variant_key="30130:100",
        name="Widal test",
        specimen="Serum",
        method="Card응집법",
        schedule="월~토/주간",
        tat="1일",
        source_title="SCL 검사항목조회",
        source_url="https://www.scllab.co.kr/front/check/check_item_detail.do?itemcode=30130&sampcode=100",
        updated_at="2026-08-25",
        demo=False,
        public_details={"채취방법 및 주의사항": "검체 보관 시 유의사항"},
    )
    plan = ModelPlan(
        domain="test",
        sub_intent="request_preparation_guide",
        requested_action="explain",
        answer="Widal test 공개 주의사항입니다.",
        supporting_test_variant_keys=["30130:100"],
    )

    reply, matched = make_orchestrator()._reply_from_plan(
        plan,
        trusted_hits={},
        trusted_tests=[test],
    )

    assert matched is None
    assert reply.data_status == "public_database"
    assert [citation.ref for citation in reply.citations] == ["test:30130:100"]


def test_uses_candidate_codes_for_clarification_choices() -> None:
    plan = ModelPlan(
        domain="test",
        sub_intent="search_by_condition",
        requested_action="search",
        answer="갑상선 검사 후보가 여러 개입니다.",
        candidate_test_codes=["DEMO-D3230", "DEMO-D3240"],
        needs_clarification=True,
        clarification_question="어떤 검사를 확인할까요?",
        confidence=0.75,
    )

    reply, matched = make_orchestrator()._reply_from_plan(plan)

    assert matched is None
    assert reply.kind == "choices"
    assert reply.choices == ["TSH · 혈청", "Free T4 · 혈청"]


def test_does_not_resolve_test_code_outside_test_domain() -> None:
    plan = ModelPlan(
        domain="result",
        sub_intent="interpret_personal_result",
        requested_action="explain",
        answer="개인 검사결과는 의료진과 상담해 주세요.",
        matched_test_code="DEMO-C5621",
        requires_authentication=True,
        needs_handoff=True,
        medical_review_required=True,
        confidence=0.94,
    )

    reply, matched = make_orchestrator()._reply_from_plan(plan)

    assert matched is None
    assert reply.kind == "result_auth_form"


def _seed_routing_public_data() -> tuple[int, int]:
    with SessionLocal.begin() as session:
        source = session.query(DataSource).filter_by(key="ROUTING_TEST").first()
        if source is None:
            source = DataSource(key="ROUTING_TEST", name="routing", base_url="https://www.scllab.co.kr")
            session.add(source)
            session.flush()
        document = session.query(PublicDocument).filter_by(source_external_key="routing-doc").first()
        if document is None:
            document = PublicDocument(
                data_source_id=source.id,
                source_external_key="routing-doc",
                document_type="official_notice",
                title="설 연휴 검사일정 안내",
                normalized_title=normalize_search_text("설 연휴 검사일정 안내"),
                summary="설 연휴에는 검사 일정이 변경됩니다.",
                source_url="https://www.scllab.co.kr/front/notice",
                content_hash="d" * 64,
            )
            session.add(document)
            session.flush()
        location = session.query(ServiceLocation).filter_by(source_external_key="routing-busan").first()
        if location is None:
            location = ServiceLocation(
                data_source_id=source.id,
                source_external_key="routing-busan",
                name="부산테스트센터",
                region="부산",
                address="부산광역시 테스트로 1",
                phone="051-000-0000",
                source_url="https://www.scllab.co.kr/front/location",
                content_hash="e" * 64,
            )
            session.add(location)
            session.flush()
        return document.id, location.id


def test_resolves_structured_document_ref_to_database_citation() -> None:
    document_id, _ = _seed_routing_public_data()
    plan = ModelPlan(
        domain="document",
        sub_intent="search_schedule_notice",
        requested_action="search",
        answer="설 연휴 검사일정 안내입니다.",
        matched_document_ids=[f"document:{document_id}"],
        confidence=0.9,
    )
    reply, matched = make_orchestrator()._reply_from_plan(plan)
    assert matched is None
    assert reply.data_status == "public_document"
    assert reply.citations and reply.citations[0].title == "설 연휴 검사일정 안내"


def test_demo_mode_uses_public_location_database() -> None:
    _seed_routing_public_data()
    reply, domain = make_orchestrator()._demo_reply(
        "부산테스트센터 전화번호", make_orchestrator().sessions.get(None)[1]
    )
    assert domain == "support"
    assert reply.data_status == "public_database"
    assert "051-000-0000" in reply.text


def test_rejects_model_authored_citation_without_database_reference() -> None:
    plan = ModelPlan(
        domain="document",
        sub_intent="search_general_notice",
        requested_action="search",
        answer="모델이 만든 공지입니다.",
        citations=[
            ModelCitation(
                title="가짜 공지",
                url="https://www.scllab.co.kr/front/fabricated-notice",
            )
        ],
        confidence=0.8,
    )

    reply, matched = make_orchestrator()._reply_from_plan(plan)

    assert matched is None
    assert reply.citations == []
    assert reply.data_status == "no_source"
    assert "모델이 만든 공지" not in reply.text


def test_rejects_existing_test_that_was_not_in_the_retrieved_candidates() -> None:
    plan = ModelPlan(
        domain="test",
        sub_intent="get_test_detail",
        requested_action="explain",
        answer="존재하지만 질문과 무관한 검사입니다.",
        matched_test_code="DEMO-C5621",
    )

    reply, matched = make_orchestrator()._reply_from_plan(
        plan,
        trusted_hits={},
        trusted_tests=[],
    )

    assert matched is None
    assert reply.answerability == "none"
    assert "질문과 무관한" not in reply.text


def test_verified_document_uses_server_text_instead_of_model_claim() -> None:
    document_id, _ = _seed_routing_public_data()
    plan = ModelPlan(
        domain="document",
        sub_intent="search_schedule_notice",
        requested_action="search",
        answer="문서에 없는 휴무일은 99일입니다.",
        matched_document_ids=[f"document:{document_id}"],
    )

    reply, _ = make_orchestrator()._reply_from_plan(plan)

    assert reply.grounding_status == "grounded_internal"
    assert "99일" not in reply.text
    assert "관련 공개 문서를 찾았습니다" in reply.text


def test_rejects_unresolvable_model_database_reference_and_answer() -> None:
    plan = ModelPlan(
        domain="document",
        sub_intent="search_general_notice",
        requested_action="search",
        answer="존재하지 않는 공문 내용을 사실처럼 답합니다.",
        matched_document_ids=["document:99999999"],
        confidence=0.8,
    )

    reply, matched = make_orchestrator()._reply_from_plan(plan)

    assert matched is None
    assert reply.citations == []
    assert reply.data_status == "no_source"
    assert "존재하지 않는 공문" not in reply.text


def test_rejects_unresolvable_test_candidate_choices() -> None:
    plan = ModelPlan(
        domain="test",
        sub_intent="search_by_condition",
        requested_action="search",
        answer="후보가 있습니다.",
        candidate_test_codes=["FABRICATED-TEST"],
        needs_clarification=True,
        choices=["가짜 검사"],
        confidence=0.6,
    )

    reply, matched = make_orchestrator()._reply_from_plan(plan)

    assert matched is None
    assert reply.kind == "text"
    assert reply.choices == []
    assert reply.data_status == "no_source"


def test_candidate_codes_and_variant_keys_are_not_treated_as_parallel_arrays() -> None:
    plan = ModelPlan(
        domain="test",
        sub_intent="search_by_condition",
        requested_action="search",
        answer="후보가 여러 개입니다.",
        candidate_test_codes=["DEMO-D3230", "DEMO-D3240"],
        candidate_test_variant_keys=["DEMO-D3230:DEMO", "DEMO-D3240:DEMO"],
        needs_clarification=True,
        clarification_question="어떤 검사를 확인할까요?",
        confidence=0.8,
    )

    reply, matched = make_orchestrator()._reply_from_plan(plan)

    assert matched is None
    assert reply.choices == ["TSH · 혈청", "Free T4 · 혈청"]


def test_rejects_mismatched_test_code_and_variant_key() -> None:
    plan = ModelPlan(
        domain="test",
        sub_intent="get_test_detail",
        requested_action="explain",
        answer="잘못 연결된 검사입니다.",
        matched_test_code="DEMO-D3230",
        matched_test_variant_key="DEMO-D3240:DEMO",
        confidence=0.9,
    )

    reply, matched = make_orchestrator()._reply_from_plan(plan)

    assert matched is None
    assert reply.data_status == "no_source"
    assert "잘못 연결된 검사" not in reply.text
