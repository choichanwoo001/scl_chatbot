from app.answer_coverage import apply_test_answer_coverage
from app.schemas import Reply
from app.schemas import TestInfo as SclTestInfo


def _test_info() -> SclTestInfo:
    return SclTestInfo(
        code="16290",
        variant_key="16290:510",
        name="Fabry 검사",
        specimen="Heparin W/B",
        container=None,
        method="LC-MS/MS",
        schedule="월~목",
        tat="5일",
        source_title="SCL 검사항목조회",
        updated_at="2026-09-17",
        demo=False,
        public_details={"급여코드": "D517205KZ"},
    )


def test_requested_fields_are_answered_before_the_generic_card_text() -> None:
    reply = apply_test_answer_coverage(
        Reply(text="확인된 검사입니다.", answerability="full", claim_coverage=1),
        ["billing", "tat"],
        _test_info(),
    )

    assert reply.text.startswith("요청하신 정보")
    assert "급여코드: D517205KZ" in reply.text
    assert "소요일: 5일" in reply.text
    assert reply.answerability == "full"
    assert reply.claim_coverage == 1


def test_missing_requested_field_downgrades_full_answer() -> None:
    reply = apply_test_answer_coverage(
        Reply(text="확인된 검사입니다.", answerability="full", claim_coverage=1),
        ["billing", "container"],
        _test_info(),
    )

    assert reply.answerability == "partial"
    assert reply.claim_coverage == 0.5
    assert reply.missing_information == ["Fabry 검사: 용기"]
