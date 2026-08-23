from app.config import Settings
from app.database import SessionLocal
from app.models import ChatFeedback, FAQCandidate, HandoffRequest
from app.schemas import FeedbackCreateRequest, HandoffCreateRequest
from app.secure_workflows import WorkflowService


def _service() -> WorkflowService:
    return WorkflowService(Settings(openai_api_key=None))


def test_handoff_is_stored_encrypted_and_returns_receipt() -> None:
    service = _service()
    receipt = service.create_handoff(
        HandoffCreateRequest(
            session_id="session-safe",
            inquiry_type="specimen_shipping",
            requester_name="홍길동",
            phone="010-1234-5678",
            organization="테스트의원",
            content="검체 배송 문의입니다.",
            related_refs=["test:DEMO-C5621", "https://evil.example"],
            consent=True,
        )
    )

    assert receipt.public_id.startswith("SCL-")
    with SessionLocal() as session:
        item = session.query(HandoffRequest).filter_by(public_id=receipt.public_id).one()
        assert "홍길동" not in item.requester_name_encrypted
        assert "010-1234-5678" not in item.phone_encrypted
        assert service.cipher.decrypt(item.requester_name_encrypted) == "홍길동"
        assert service.cipher.decrypt(item.phone_encrypted) == "010-1234-5678"
        assert item.related_refs == ["test:DEMO-C5621"]


def test_feedback_merges_duplicate_faq_candidates_and_redacts_pii() -> None:
    service = _service()
    base = dict(
        session_id="feedback-session",
        response_id="resp-1",
        reason=None,
        comment="연락처 010-9999-8888",
        question="HPV 검사 용기는 무엇인가요?",
        answer="전용 수송용기입니다.",
        domain="test",
        sub_intent="get_test_detail",
        source_refs=["test:DEMO-C5621"],
    )
    first = service.create_feedback(FeedbackCreateRequest(rating="helpful", **base))
    second = service.create_feedback(FeedbackCreateRequest(rating="not_helpful", **base))

    assert first.faq_candidate_id == second.faq_candidate_id
    assert second.merged_occurrences == first.merged_occurrences + 1
    with SessionLocal() as session:
        candidate = session.get(FAQCandidate, first.faq_candidate_id)
        feedback = session.query(ChatFeedback).order_by(ChatFeedback.id.desc()).first()
        assert candidate is not None
        assert candidate.positive_count >= 1
        assert candidate.negative_count >= 1
        assert feedback is not None
        assert "010-9999-8888" not in (feedback.comment or "")
