from __future__ import annotations

import re
from dataclasses import dataclass

PROFANITY_PATTERN = re.compile(
    r"(씨\s*발|시\s*발|개\s*새\s*끼|병\s*신|꺼\s*져|ㅅ\s*ㅂ|ㅂ\s*ㅅ)", re.IGNORECASE
)
SUPPORTED_INTENT_PATTERN = re.compile(
    r"(검사|검체|용기|소요일|코드|hpv|갑상선|혈액|소변|결과|의뢰|장비|배송)", re.IGNORECASE
)
PROMPT_INJECTION_PATTERN = re.compile(
    r"((이전|앞의).*(지시|규칙).*(무시|잊어)|ignore\s+(all\s+)?previous|system\s*prompt|"
    r"시스템\s*프롬프트|내부\s*(지침|규칙)|개발자\s*메시지|jailbreak)",
    re.IGNORECASE,
)
RESIDENT_ID_PATTERN = re.compile(r"(?<!\d)\d{6}\s*-\s*[1-4]\d{6}(?!\d)")
PHONE_PATTERN = re.compile(r"(?<!\d)01[016789][\s.-]?\d{3,4}[\s.-]?\d{4}(?!\d)")
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
REPEATED_PATTERN = re.compile(r"(.)\1{14,}")


@dataclass(frozen=True)
class InputInspection:
    category: str
    action: str
    displayed_input: str
    model_input: str


def inspect_input(value: str, max_chars: int = 500) -> InputInspection:
    normalized = re.sub(r"\s+", " ", value).strip()[:max_chars]
    redacted = RESIDENT_ID_PATTERN.sub("[주민등록번호 가림]", normalized)
    redacted = PHONE_PATTERN.sub("[전화번호 가림]", redacted)
    redacted = EMAIL_PATTERN.sub("[이메일 가림]", redacted)

    if PROMPT_INJECTION_PATTERN.search(normalized):
        return InputInspection("prompt_injection", "block", redacted, redacted)

    if redacted != normalized:
        return InputInspection("personal_data", "redact", redacted, redacted)

    if REPEATED_PATTERN.search(normalized):
        return InputInspection("repeated_input", "block", redacted, redacted)

    if PROFANITY_PATTERN.search(normalized):
        if SUPPORTED_INTENT_PATTERN.search(normalized):
            return InputInspection("profanity_with_intent", "warn", redacted, redacted)
        return InputInspection("profanity_only", "block", redacted, redacted)

    return InputInspection("safe", "allow", redacted, redacted)


def safe_citation_url(value: str | None) -> str | None:
    if not value:
        return None
    if re.match(r"^https://([a-z0-9-]+\.)*scllab\.co\.kr(?:/|$)", value, re.IGNORECASE):
        return value
    return None
