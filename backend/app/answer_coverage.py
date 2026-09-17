from __future__ import annotations

from .schemas import Reply, TestInfo

FIELD_SLOTS = {
    "billing": ("급여코드", lambda item: item.public_details.get("급여코드")),
    "specimen": ("검체", lambda item: item.specimen),
    "container": ("용기", lambda item: item.container),
    "method": ("검사방법", lambda item: item.method),
    "schedule": ("검사요일", lambda item: item.schedule),
    "tat": ("소요일", lambda item: item.tat),
    "precautions": (
        "주의사항",
        lambda item: item.public_details.get("채취방법 및 주의사항"),
    ),
    "clinical_significance": (
        "임상적 의의",
        lambda item: item.public_details.get("임상적 의의"),
    ),
    "storage": ("검체 보존방법", lambda item: item.public_details.get("보존방법")),
    "price": ("공개 검사수가", lambda item: item.public_details.get("검사수가")),
}


def apply_test_answer_coverage(
    reply: Reply,
    requested_fields: list[str],
    test: TestInfo | None,
) -> Reply:
    """Make requested test fields explicit and prevent false full answers."""
    slots = [field for field in dict.fromkeys(requested_fields) if field in FIELD_SLOTS]
    if not slots or test is None:
        return reply

    facts: list[str] = []
    missing = list(reply.missing_information)
    covered = 0
    for field in slots:
        label, getter = FIELD_SLOTS[field]
        value = getter(test)
        if value and str(value).strip() != "-":
            facts.append(f"- {label}: {str(value).strip()}")
            covered += 1
        else:
            missing.append(f"{test.name}: {label}")

    coverage = covered / len(slots)
    prefix = "요청하신 정보\n" + "\n".join(facts)
    if missing:
        prefix += "\n\n확인하지 못한 항목\n- " + "\n- ".join(dict.fromkeys(missing))
    reply.text = f"{prefix}\n\n{reply.text}" if reply.text else prefix
    reply.claim_coverage = coverage
    reply.missing_information = list(dict.fromkeys(missing))
    if missing and reply.answerability == "full":
        reply.answerability = "partial" if covered else "none"
    return reply
