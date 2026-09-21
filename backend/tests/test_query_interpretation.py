import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from app.chat_contracts import ModelPlan, RetrievalContext
from app.config import Settings
from app.normalization import normalize_search_text
from app.orchestrator import ChatOrchestrator
from app.query_interpretation import (
    Identifier,
    QueryInterpretation,
    code_candidates,
    execute_query,
    normalize_interpretation,
    query_reply,
    validate_query,
)
from app.schemas import TestInfo as CatalogTestInfo

PHRASES = [
    "{c}",
    "{c} 보험코드",
    "{c} 보험코드를 조회해줘",
    "{c} 해당하는 보험코드를 가진 검사 조회좀",
    "아니 그럼 급여코드 {c} 이걸로 조회",
    "급여코드 {c}로 검사 찾아줘",
]


def row(code, billing, specimen="urine", tat="2일", suffix="1"):
    info = CatalogTestInfo(
        code=code,
        variant_key=f"{code}:{suffix}",
        name=f"검사 {code}",
        specimen=specimen,
        method="PCR",
        schedule="월",
        tat=tat,
        source_title="test",
        updated_at="2026-09-13",
        public_details={"급여코드": billing},
        demo=False,
    )
    return SimpleNamespace(
        info=info,
        billing=billing.lower(),
        specimen=specimen,
        specimen_terms=specimen,
        method="pcr",
        name=normalize_search_text(info.name),
        aliases=(),
    )


@pytest.fixture
def db(monkeypatch):
    from app.catalog import catalog

    rows = [
        row("16290", "D517205KZ"),
        row("16290", "D517205KZ", "serum", "5일", "2"),
        row("12345", "D123456AB", "serum", "1일"),
    ]
    # Distractors deliberately contain formerly high-scoring conversational words.
    rows += [row(str(20000 + i), "Z999999ZZ") for i in range(30)]
    monkeypatch.setattr(catalog, "search_rows", lambda: tuple(rows))
    return catalog


@pytest.mark.parametrize("phrase", PHRASES)
def test_particle_and_word_order_invariance(db, phrase):
    message = phrase.format(c="D517205KZ")
    query = QueryInterpretation(intent="test", identifiers=[Identifier(kind="billing", value="D517205KZ")])
    assert validate_query(query, message, []) is None
    items, exhaustive = execute_query(db, query, [])
    assert {i.variant_key for i in items} == {"16290:1", "16290:2"}
    assert exhaustive


def test_unknown_code_does_not_search_keywords(db, monkeypatch):
    monkeypatch.setattr(db, "search", lambda *a, **k: pytest.fail("exact lookup must not score keywords"))
    query = QueryInterpretation(intent="test", identifiers=[Identifier(kind="billing", value="X000000ZZ")])
    items, exhaustive = execute_query(db, query, [])
    assert items == []
    assert query_reply(query, items, exhaustive, None).data_status == "no_source"


def test_exclusion_and_specimen_filter(db):
    message = "D517205KZ 말고 D123456AB로 혈청 검사"
    query = QueryInterpretation(
        intent="test",
        identifiers=[
            Identifier(kind="billing", value="D517205KZ", exclude=True),
            Identifier(kind="billing", value="D123456AB"),
        ],
        specimen="serum",
        specimen_evidence="혈청",
    )
    assert validate_query(query, message, []) is None
    assert [i.code for i in execute_query(db, query, [])[0]] == ["12345"]


def test_filters_run_before_limit_and_keep_variants(db):
    query = QueryInterpretation(
        intent="test",
        identifiers=[Identifier(kind="billing", value="D517205KZ")],
        specimen="serum",
        specimen_evidence="혈청",
    )
    items, _ = execute_query(db, query, [])
    assert [i.variant_key for i in items] == ["16290:2"]
    query.specimen = ""
    query.max_tat_days = 3
    assert [i.variant_key for i in execute_query(db, query, [])[0]] == ["16290:1"]


def test_previous_results_and_shortest_tat(db):
    previous = [r.info for r in db.search_rows()[:2]]
    query = QueryInterpretation(
        intent="test", use_previous_results=True, shortest_tat=True, requested_fields=["tat"]
    )
    assert validate_query(query, "그중 가장 빠른 건?", previous) is None
    items, exhaustive = execute_query(db, query, previous)
    assert [i.variant_key for i in items] == ["16290:1"]
    assert "2일" in query_reply(query, items, exhaustive, None).text


@pytest.mark.parametrize(
    "query,message",
    [
        (
            QueryInterpretation(intent="test", identifiers=[Identifier(kind="billing", value="D123456AB")]),
            "D517205KZ",
        ),
        (QueryInterpretation(intent="test", search_text="TSH"), "D517205KZ"),
        (QueryInterpretation(intent="test", use_previous_results=True), "그 검사"),
        (
            QueryInterpretation(intent="test", use_previous_results=True, previous_variant_keys=["invented"]),
            "그 검사",
        ),
        (
            QueryInterpretation(intent="test", search_text="TSH", specimen="urine", specimen_evidence="소변"),
            "TSH",
        ),
        (
            QueryInterpretation(intent="test", search_text="TSH", unsupported_conditions=["가격 1만원 이하"]),
            "TSH 1만원 이하",
        ),
    ],
)
def test_invalid_or_unsupported_query_abstains(query, message):
    assert validate_query(query, message, [])


def test_identifier_boundaries_and_nfkc():
    assert code_candidates("급여코드 Ｄ５１７２０５ＫＺ로 검사") == {"D517205KZ"}
    assert not code_candidates("XD517205KZ D517205KZX 1162900")
    assert code_candidates("16290으로 검사") == {"16290"}
    assert code_candidates("R0329와 8A088 검사") == {"R0329", "8A088"}


def test_bare_code_does_not_trust_model_field_guess(db):
    query = QueryInterpretation(intent="test", identifiers=[Identifier(kind="scl", value="D517205KZ")])
    assert validate_query(query, "D517205KZ", []) is None
    assert query.identifiers[0].kind == "unknown"
    assert len(execute_query(db, query, [])[0]) == 2


def test_catalog_etc_annotation_does_not_drop_variants(db):
    db.search_rows()[1].billing = "d517205kzetc"
    db.search_rows()[1].info.public_details["급여코드"] = "D517205KZetc"
    query = QueryInterpretation(intent="test", identifiers=[Identifier(kind="billing", value="D517205KZ")])
    assert len(execute_query(db, query, [])[0]) == 2


def test_requested_billing_field_and_multiple_results(db):
    query = QueryInterpretation(
        intent="test",
        identifiers=[Identifier(kind="billing", value="D517205KZ")],
        requested_fields=["billing", "container"],
    )
    items, exhaustive = execute_query(db, query, [])
    reply = query_reply(query, items, exhaustive, None)
    assert reply.test is None
    assert "D517205KZ" in reply.text
    assert len(reply.citations) == 2
    assert reply.answerability == "partial"
    assert reply.missing_information


@pytest.mark.parametrize("phrase", PHRASES)
def test_orchestrator_interprets_before_search_and_uses_one_call(db, phrase):
    runtime = ChatOrchestrator(
        Settings(llm_provider="gemini", gemini_api_key="test", vector_search_enabled=False)
    )
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        context = json.loads(payload["contents"][-1]["parts"][0]["text"])
        assert context["code_candidates"] == ["D517205KZ"]
        requests.append(context)
        query = QueryInterpretation(
            intent="test", identifiers=[Identifier(kind="billing", value="D517205KZ")]
        )
        return httpx.Response(
            200,
            json={
                "responseId": "interpret-1",
                "candidates": [{"content": {"parts": [{"text": query.model_dump_json()}]}}],
            },
        )

    runtime.gateway.client.close()
    runtime.gateway.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        result = asyncio.run(runtime.respond(phrase.format(c="D517205KZ"), None, require_live=True))
        assert len(requests) == 1
        assert result.mode == "gemini"
        assert result.response_id == "interpret-1"
        assert "interpretation" in result.timings_ms
        assert len(result.reply.citations) == 2
        assert len(runtime.sessions.get(result.session_id)[1].previous_tests) == 2
    finally:
        runtime.gateway.client.close()


def test_gemini_safety_does_not_fall_back_to_demo(db):
    runtime = ChatOrchestrator(Settings(llm_provider="gemini", gemini_api_key="test"))
    runtime.gateway.client.close()
    runtime.gateway.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
        )
    )
    try:
        result = asyncio.run(runtime.respond("D517205KZ", None))
        assert result.safety_action == "block"
        assert result.mode == "gemini"
        assert not result.reply.citations
    finally:
        runtime.gateway.client.close()


def test_non_test_intent_retains_authenticated_result_flow(monkeypatch):
    runtime = ChatOrchestrator(Settings(llm_provider="gemini", gemini_api_key="test"))
    monkeypatch.setattr(runtime.gateway, "retrieve", lambda message: RetrievalContext((), ()))
    outputs = [
        QueryInterpretation(intent="other"),
        ModelPlan(
            domain="result",
            sub_intent="navigate_to_result",
            requested_action="navigate",
            answer="결과 조회",
        ),
    ]

    def respond(request):
        result = outputs.pop(0)
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": result.model_dump_json()}]}}]}
        )

    runtime.gateway.client.close()
    runtime.gateway.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        result = asyncio.run(runtime.respond("검사결과 어디서 봐?", None, require_live=True))
        assert result.reply.kind == "result_auth_form"
        assert result.requires_authentication
        assert not outputs
    finally:
        runtime.gateway.client.close()


def test_shortest_uses_upper_bound_and_does_not_claim_missing_tat_is_ranked(db):
    rows = db.search_rows()
    rows[0].info.tat = "1~5일"
    rows[1].info.tat = "2~3일"
    rows[2].info.tat = "정보 없음"
    previous = [r.info for r in rows[:3]]
    query = QueryInterpretation(intent="test", use_previous_results=True, shortest_tat=True)
    items, exhaustive = execute_query(db, query, previous)
    assert [i.variant_key for i in items] == ["16290:2"]
    assert not exhaustive
    assert query_reply(query, items, exhaustive, None).answerability == "partial"


@pytest.mark.parametrize(
    ("message", "expected_names"),
    [
        ("간 검사하려면 어떤 검사를 보면 돼?", ["ALT", "AST", "γ-GTP"]),
        ("피 뽑아서 하는 갑상선 검사 알려줘", ["TSH", "Free T4", "Free T3"]),
        ("에이엘티 검사 알려줘", []),
    ],
)
def test_normalizes_common_korean_test_expressions(message, expected_names):
    query = normalize_interpretation(QueryInterpretation(intent="other"), message, [])
    assert query.intent == "test"
    if expected_names:
        assert query.related_names[:3] == expected_names
    else:
        assert query.search_text == "ALT"


def test_normalizes_composite_specimen_tat_and_schedule_conditions():
    query = normalize_interpretation(
        QueryInterpretation(intent="test", unsupported_conditions=["토요일에도 하는 검사"]),
        "Serum으로 검사하고 하루 안에 나오며 토요일에도 하는 ALT",
        [],
    )
    assert query.search_text == "ALT"
    assert query.specimen == "serum"
    assert query.max_tat_days == 1
    assert query.schedule_days == ["토"]
    assert query.unsupported_conditions == []


def test_english_test_names_allow_korean_particles():
    query = normalize_interpretation(
        QueryInterpretation(intent="test", search_text="ALT"),
        "ALT 말고 AST도 같이 알려줘",
        [],
    )
    assert query.related_names == ["ALT", "AST"]
    assert query.search_text == ""
    assert query.compare is False


def test_comparison_reply_only_surfaces_common_and_different_fields(db):
    items = [row.info for row in db.search_rows()[:2]]
    items[0].schedule = "월~토 /주간"
    items[1].schedule = "월~토 /야간"
    items[0].public_details["채취방법 및 주의사항"] = "긴 주의사항 " * 100
    query = QueryInterpretation(intent="test", compare=True)
    reply = query_reply(query, items, True, None)
    assert "## 차이점" in reply.text
    assert "검사요일" in reply.text
    assert "상세 안내 있음" in reply.text
    assert "긴 주의사항" not in reply.text
    assert len(reply.citations) == 2


def test_comparison_uses_source_specific_missing_wording(db):
    items = [row.info for row in db.search_rows()[:2]]
    items[1].public_details["채취방법 및 주의사항"] = "안내 있음"

    reply = query_reply(QueryInterpretation(intent="test", compare=True), items, True, None)

    assert "SCL 공개 페이지에 별도 주의사항 미기재" in reply.text
    assert "공개 데이터에 없음" not in reply.text


def test_container_field_uses_grounded_container_metadata(db):
    item = db.search_rows()[0].info
    item.public_details["용기 첨가제"] = "PBS"
    item.public_details["용기 주요검사항목"] = "HPV, STD"
    reply = query_reply(
        QueryInterpretation(intent="test", requested_fields=["container"]),
        [item],
        True,
        None,
    )
    assert "첨가제: PBS" in reply.text
    assert "공개 데이터에 없음" not in reply.text


def test_multiple_linked_thyroid_tests_include_a_common_explanation(db):
    items = [row.info for row in db.search_rows()[:2]]
    query = QueryInterpretation(
        intent="test",
        related_names=["TSH", "Free T4", "Free T3", "T3", "T4"],
    )

    reply = query_reply(query, items, True, None)

    assert "갑상선 기능을 확인할 때 함께 참고하는 검사" in reply.text
    assert "TSH는 갑상선자극호르몬" in reply.text
    assert len(reply.citations) == 2


def test_other_multiple_linked_tests_include_a_grounded_common_explanation(db):
    items = [row.info for row in db.search_rows()[:2]]
    items[0].specimen = "Serum"
    items[1].specimen = "Serum"

    reply = query_reply(QueryInterpretation(intent="test"), items, True, None)

    assert "질문 조건에 맞는 SCL 검사 상세 페이지" in reply.text
    assert "모두 검체가 Serum인 검사" in reply.text


def test_candidate_field_request_is_not_treated_as_unsupported():
    query = normalize_interpretation(
        QueryInterpretation(
            intent="test",
            unsupported_conditions=["후보별로 검사코드와 검체를 같이 보여줘"],
        ),
        "갑상선 검사 후보별로 검사코드와 검체를 같이 보여줘",
        [],
    )

    assert query.answer_mode == "list"
    assert query.related_names[:3] == ["TSH", "Free T4", "Free T3"]
    assert query.requested_fields == ["specimen"]
    assert query.unsupported_conditions == []


def test_hpv_container_question_uses_direct_field_answer_mode():
    query = normalize_interpretation(
        QueryInterpretation(intent="test", search_text="HPV"),
        "HPV 검사할 때 어떤 통에 담아야 해?",
        [],
    )

    assert query.requested_fields == ["container"]
    assert query.answer_mode == "field_answer"


def test_hpv_multi_field_question_keeps_every_explicit_field():
    query = normalize_interpretation(
        QueryInterpretation(intent="test", search_text="HPV", requested_fields=["method", "tat"]),
        "HPV 검사의 검체, 용기, 검사방법, 소요일을 한 번에 알려줘",
        [],
    )

    assert query.answer_mode == "field_answer"
    assert query.requested_fields == ["specimen", "container", "method", "tat"]


def test_hpv_container_answer_groups_by_collection_type(db):
    query = normalize_interpretation(
        QueryInterpretation(intent="test", search_text="HPV"),
        "HPV 검사할 때 어떤 통에 담아야 해?",
        [],
    )
    items = [
        CatalogTestInfo(
            code="39313",
            variant_key="39313:515",
            name="HPV genotyping(Real-time PCR)",
            specimen="Vaginal/Cervical Swab",
            method="Real-time PCR",
            schedule="월~토",
            tat="1일",
            source_title="SCL",
            updated_at="2026-08-20",
            public_details={"용기 첨가제": "PBS(Phosphate buffer solution)", "용기 주요검사항목": "HPV, STD"},
            demo=False,
        ),
        CatalogTestInfo(
            code="38940",
            variant_key="38940:515",
            name="HPV screening PCR",
            specimen="Vaginal/Cervical Swab",
            method="PCR",
            schedule="월~토",
            tat="1일",
            source_title="SCL",
            updated_at="2026-08-20",
            public_details={"용기 첨가제": "PBS(Phosphate buffer solution)", "용기 주요검사항목": "HPV, STD"},
            demo=False,
        ),
        CatalogTestInfo(
            code="39313",
            variant_key="39313:701",
            name="액상 HPV genotyping(Real-time PCR)",
            specimen="Cervix cell",
            method="Real-time PCR",
            schedule="월~토",
            tat="1일",
            source_title="SCL",
            updated_at="2026-08-20",
            public_details={"용기 첨가제": "세포보존제", "용기 주요검사항목": "액상 자궁경부세포검사"},
            demo=False,
        ),
    ]
    reply = query_reply(query, items, False, None)

    assert "검사 방식과 검체에 따라 사용하는 용기가 다릅니다" in reply.text
    assert "자궁경부·질 면봉을 이용한 HPV PCR" in reply.text
    assert "액상 HPV 검사" in reply.text
    assert "PBS(Phosphate buffer solution)" in reply.text
    assert "세포보존제" in reply.text
    assert "p16 (IHC)" not in reply.text
    assert "액상자궁경부세포검사" not in reply.text
    assert reply.text.count("PBS(Phosphate buffer solution)") == 1
    assert "용기: 첨가제" not in reply.text
    assert len(reply.citations) == 2


def test_multi_field_group_does_not_repeat_specimen_label(db):
    items = [row.info for row in db.search_rows()[:2]]
    items[0].specimen = "Serum"
    items[1].specimen = "Serum"
    query = QueryInterpretation(
        intent="test",
        answer_mode="field_answer",
        requested_fields=["specimen", "method", "tat"],
    )

    reply = query_reply(query, items, True, None)

    assert "Serum 검체:" in reply.text
    assert "검체: Serum" not in reply.text


def test_explanation_request_overrides_model_inferred_clinical_field(db):
    query = normalize_interpretation(
        QueryInterpretation(
            intent="test",
            search_text="α1-Antitrypsin",
            requested_fields=["clinical_significance"],
        ),
        "R0329가 무슨 검사야?",
        [],
    )
    item = db.search_rows()[0].info
    item.code = "R0329"
    item.name = "α1-Antitrypsin"
    item.specimen = "Stool"
    item.method = "ELISA"

    reply = query_reply(query, [item], True, None)

    assert query.answer_mode == "explanation"
    assert "R0329는 α1-Antitrypsin 검사입니다" in reply.text
    assert "검체는 Stool" in reply.text


def test_broad_results_show_only_representative_eight(db):
    items = [row.info for row in db.search_rows()[:12]]

    reply = query_reply(QueryInterpretation(intent="test"), items, True, None)

    assert "대표 결과 8건만 표시합니다" in reply.text
    assert len(reply.citations) == 8
    assert reply.text.count("(검사코드") == 8


def test_long_detail_fields_are_compacted(db):
    item = db.search_rows()[0].info
    item.public_details["채취방법 및 주의사항"] = "긴 주의사항입니다. " * 100
    item.public_details["임상적 의의"] = "긴 임상 설명입니다. " * 100
    query = QueryInterpretation(
        intent="test",
        requested_fields=["precautions", "clinical_significance"],
    )

    reply = query_reply(query, [item], True, None)

    assert len(reply.text) < 700
    assert " …" in reply.text


@pytest.mark.parametrize(
    ("message", "expected_mode", "expected_field"),
    [
        ("ALT 결과는 언제 나와?", "field_answer", "tat"),
        ("ALT는 무슨 검체야?", "field_answer", "specimen"),
        ("ALT 검사방법이 뭐야?", "field_answer", "method"),
        ("ALT 검사를 찾아줘", "list", None),
        ("ALT가 무슨 검사야?", "explanation", None),
    ],
)
def test_answer_mode_follows_the_user_goal(message, expected_mode, expected_field):
    query = normalize_interpretation(
        QueryInterpretation(intent="test", search_text="ALT"),
        message,
        [],
    )

    assert query.answer_mode == expected_mode
    if expected_field:
        assert expected_field in query.requested_fields


def test_schedule_condition_search_takes_priority_over_explanation_wording():
    query = normalize_interpretation(
        QueryInterpretation(intent="test"),
        "간수치 검사 중 토요일에도 하는 검사가 뭐야?",
        [],
    )

    assert query.answer_mode == "list"
    assert query.schedule_days == ["토"]
    assert "schedule" in query.requested_fields


def test_shortest_search_takes_priority_and_requests_tat():
    query = normalize_interpretation(
        QueryInterpretation(intent="test"),
        "결과 빨리 나오는 갑상선 관련 검사",
        [],
    )

    assert query.answer_mode == "list"
    assert query.shortest_tat is True
    assert "tat" in query.requested_fields


def test_shortest_reply_states_shared_fastest_tat(db):
    items = [row.info for row in db.search_rows()[:2]]
    items[0].tat = "1일"
    items[1].tat = "1일"
    query = QueryInterpretation(
        intent="test",
        shortest_tat=True,
        requested_fields=["tat"],
        related_names=["TSH", "Free T4"],
    )

    reply = query_reply(query, items, True, None)

    assert "가장 빠른 검사는 2건" in reply.text
    assert "모두 1일로 공동 최단" in reply.text
    assert reply.text.count("소요일: 1일") == 2
