import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from app.config import Settings
from app.normalization import normalize_search_text
from app.openai_gateway import ModelPlan, RetrievalContext
from app.orchestrator import ChatOrchestrator
from app.query_interpretation import (
    Identifier,
    QueryInterpretation,
    code_candidates,
    execute_query,
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
