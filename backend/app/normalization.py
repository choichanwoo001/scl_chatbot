from __future__ import annotations

import re
import unicodedata

DAY_NUMBERS = {"월": 1, "화": 2, "수": 3, "목": 4, "금": 5, "토": 6, "일": 7}


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()


def normalize_search_text(value: str | None) -> str:
    normalized = clean_text(value).casefold()
    return re.sub(r"[^0-9a-z가-힣α-ω]+", " ", normalized).strip()


def parse_turnaround_days(value: str | None) -> tuple[float | None, float | None]:
    text = clean_text(value)
    if not text or text == "-":
        return None, None
    if "당일" in text:
        return 0.0, 0.0

    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None, None

    multiplier = 1.0
    if "시간" in text and "일" not in text:
        multiplier = 1 / 24
    elif "주" in text and "일" not in text:
        multiplier = 7.0
    elif "개월" in text or ("월" in text and "일" not in text):
        multiplier = 30.0

    values = [number * multiplier for number in numbers]
    return min(values), max(values)


def parse_schedule(value: str | None) -> tuple[list[int], str | None]:
    text = clean_text(value)
    if not text:
        return [], None

    shift = "night" if "야간" in text else "day" if "주간" in text else None
    if "매일" in text:
        return list(range(1, 8)), shift

    days: set[int] = set()
    for start, end in re.findall(r"([월화수목금토일])\s*[~\-]\s*([월화수목금토일])", text):
        start_num, end_num = DAY_NUMBERS[start], DAY_NUMBERS[end]
        if start_num <= end_num:
            days.update(range(start_num, end_num + 1))
        else:
            days.update(range(start_num, 8))
            days.update(range(1, end_num + 1))

    range_stripped = re.sub(r"([월화수목금토일])\s*[~\-]\s*([월화수목금토일])", "", text)
    days.update(DAY_NUMBERS[item] for item in re.findall(r"[월화수목금토일]", range_stripped))
    return sorted(days), shift


def specimen_group(value: str | None) -> str | None:
    text = normalize_search_text(value)
    rules = [
        ("urine", ("urine", "소변")),
        ("serum", ("serum", "혈청")),
        ("plasma", ("plasma", "혈장")),
        ("whole_blood", ("w b", "whole blood", "전혈")),
        ("stool", ("stool", "분변", "대변")),
        ("tissue", ("tissue", "조직")),
        ("swab", ("swab", "도말")),
        ("csf", ("csf", "뇌척수액")),
        ("body_fluid", ("body fluid", "체액")),
    ]
    for group, terms in rules:
        if any(term in text for term in terms):
            return group
    return None


def changed_fields(before: dict[str, object], after: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            result[key] = {"before": before.get(key), "after": after.get(key)}
    return result
