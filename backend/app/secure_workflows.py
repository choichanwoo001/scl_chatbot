from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime

from cryptography.fernet import Fernet
from sqlalchemy import select

from .config import ROOT, Settings
from .database import SessionLocal, init_database
from .guardrails import inspect_input
from .models import ChatFeedback, FAQCandidate, HandoffRequest
from .normalization import normalize_search_text
from .schemas import FeedbackCreateRequest, FeedbackReceipt, HandoffCreateRequest, HandoffReceipt

PHONE_PATTERN = re.compile(r"^(?:\+?82[- ]?)?0?1(?:0|1|6|7|8|9)[- ]?\d{3,4}[- ]?\d{4}$")
SAFE_REF_PATTERN = re.compile(
    r"^(?:test:[A-Za-z0-9:_-]+|document:\d+|container:\d+|preservative:\d+|"
    r"location:\d+|route:\d+|taxonomy:\d+|attachment:\d+|faq:\d+)$"
)


class SensitiveFieldCipher:
    def __init__(self, settings: Settings) -> None:
        raw_key = settings.field_encryption_key
        if settings.app_environment == "production" and not raw_key:
            raise ValueError("FIELD_ENCRYPTION_KEY is required in production")
        if raw_key:
            key = raw_key.encode("ascii")
        else:
            key_path = ROOT / "data" / ".field-encryption.key"
            key_path.parent.mkdir(parents=True, exist_ok=True)
            if key_path.exists():
                key = key_path.read_bytes().strip()
            else:
                key = Fernet.generate_key()
                key_path.write_bytes(key)
        self._fernet = Fernet(key)

    def encrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")


def _session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _safe_refs(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if SAFE_REF_PATTERN.fullmatch(value)))[:10]


def _redact(value: str, max_chars: int) -> str:
    return inspect_input(value, max_chars).displayed_input


def _tokens(value: str) -> set[str]:
    return {token for token in normalize_search_text(value).split() if len(token) > 1}


def _similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


class WorkflowService:
    def __init__(self, settings: Settings, session_factory=None) -> None:
        self.session_factory = session_factory or SessionLocal
        init_database(self.session_factory.kw["bind"])
        self.cipher = SensitiveFieldCipher(settings)

    def create_handoff(self, payload: HandoffCreateRequest) -> HandoffReceipt:
        if not payload.consent:
            raise ValueError("개인정보 수집 동의가 필요합니다.")
        normalized_phone = re.sub(r"\s+", "", payload.phone)
        if not PHONE_PATTERN.fullmatch(normalized_phone):
            raise ValueError("연락처 형식을 확인해 주세요.")
        now = datetime.now(UTC)
        public_id = f"SCL-{now:%Y%m%d}-{secrets.token_hex(4).upper()}"
        with self.session_factory.begin() as session:
            session.add(
                HandoffRequest(
                    public_id=public_id,
                    session_hash=_session_hash(payload.session_id),
                    inquiry_type=payload.inquiry_type,
                    requester_name_encrypted=self.cipher.encrypt(payload.requester_name) or "",
                    phone_encrypted=self.cipher.encrypt(normalized_phone) or "",
                    organization_encrypted=self.cipher.encrypt(payload.organization),
                    content_encrypted=self.cipher.encrypt(payload.content) or "",
                    related_refs=_safe_refs(payload.related_refs),
                    status="submitted",
                    consented_at=now,
                    created_at=now,
                )
            )
        return HandoffReceipt(public_id=public_id, status="submitted", created_at=now.isoformat())

    def get_handoff(self, public_id: str) -> HandoffReceipt | None:
        with self.session_factory() as session:
            item = session.scalar(select(HandoffRequest).where(HandoffRequest.public_id == public_id))
            if not item:
                return None
            return HandoffReceipt(
                public_id=item.public_id,
                status=item.status,
                created_at=item.created_at.isoformat(),
            )

    def create_feedback(self, payload: FeedbackCreateRequest) -> FeedbackReceipt:
        question = _redact(payload.question, 500)
        answer = _redact(payload.answer, 3000)
        comment = _redact(payload.comment or "", 500) or None
        normalized = normalize_search_text(question)
        if not normalized:
            raise ValueError("FAQ 후보로 저장할 질문이 없습니다.")
        refs = _safe_refs(payload.source_refs)
        fingerprint_seed = "|".join(
            [payload.domain or "", payload.sub_intent or "", normalized, *sorted(refs)]
        )
        fingerprint = hashlib.sha256(fingerprint_seed.encode("utf-8")).hexdigest()
        with self.session_factory.begin() as session:
            candidate = session.scalar(select(FAQCandidate).where(FAQCandidate.fingerprint == fingerprint))
            if candidate is None:
                same_branch = list(
                    session.scalars(
                        select(FAQCandidate).where(
                            FAQCandidate.domain == payload.domain,
                            FAQCandidate.sub_intent == payload.sub_intent,
                            FAQCandidate.status.in_(["draft", "published"]),
                        )
                    )
                )
                candidate = next(
                    (
                        item
                        for item in same_branch
                        if set(item.source_refs) == set(refs)
                        and _similarity(item.normalized_question, normalized) >= 0.82
                    ),
                    None,
                )
            if candidate is None:
                candidate = FAQCandidate(
                    fingerprint=fingerprint,
                    canonical_question=question,
                    normalized_question=normalized,
                    canonical_answer=answer,
                    domain=payload.domain,
                    sub_intent=payload.sub_intent,
                    source_refs=refs,
                    occurrence_count=0,
                    positive_count=0,
                    negative_count=0,
                    status="draft",
                )
                session.add(candidate)
                session.flush()
            candidate.occurrence_count += 1
            if payload.rating == "helpful":
                candidate.positive_count += 1
            else:
                candidate.negative_count += 1
            feedback = ChatFeedback(
                response_id=payload.response_id,
                session_hash=_session_hash(payload.session_id),
                rating=payload.rating,
                reason=payload.reason,
                comment=comment,
                redacted_question=question,
                normalized_question=normalized,
                answer_text=answer,
                domain=payload.domain,
                sub_intent=payload.sub_intent,
                source_refs=refs,
            )
            session.add(feedback)
            session.flush()
            return FeedbackReceipt(
                feedback_id=feedback.id,
                faq_candidate_id=candidate.id,
                merged_occurrences=candidate.occurrence_count,
            )
