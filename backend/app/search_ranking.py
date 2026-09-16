from __future__ import annotations

from datetime import datetime
from typing import Any

from .normalization import clean_text, normalize_search_text


class SearchRanking:
    @staticmethod
    def _score(query: str, terms: list[str], title: str, extra: str) -> float:
        return SearchRanking._score_normalized(
            query,
            terms,
            normalize_search_text(title),
            normalize_search_text(extra),
        )

    @staticmethod
    def _score_normalized(
        query: str,
        terms: list[str],
        title_n: str,
        extra_n: str,
    ) -> float:
        if not query:
            return 0
        score = 0.0
        if query == title_n:
            score += 120
        elif query in title_n:
            score += 70
        for term in terms:
            if term == title_n:
                score += 45
            elif term in title_n:
                score += 24
            elif term in extra_n:
                score += 7
        return score

    @staticmethod
    def _type_boost(query: str, entity_type: str, subtype: str | None = None) -> float:
        cues = {
            "document": ["공문", "공지", "일정", "변경", "자료", "리플릿", "뉴스", "건강", "사회공헌"],
            "container": ["용기", "튜브", "채취", "보관"],
            "preservative": ["보존제", "24시간뇨", "차광"],
            "location": ["지점", "센터", "주소", "전화", "팩스", "연락처"],
            "route": ["메뉴", "페이지", "어디", "경로", "링크", "로그인"],
            "faq": ["자주", "FAQ", "문의", "질문"],
            "attachment": ["첨부", "파일", "PDF", "공문", "자료", "양식", "다운로드"],
        }
        boost = 90 if any(cue in query for cue in cues.get(entity_type, [])) else 0
        if entity_type == "document" and subtype == "official_faq":
            boost += 40
        if subtype and subtype.replace("_", " ") in query:
            boost += 10
        return boost

    @staticmethod
    def _attachment_intent_boost(query: str, terms: list[str], file_name: str, document_title: str) -> float:
        """Prefer the general SCL request form for an otherwise generic download query.

        Without this tie-breaker dozens of specialist referral forms receive the
        same score and alphabetical ordering can surface an unrelated hospital form.
        A named test (for example, AMH) remains more specific and gets no boost.
        """
        generic_terms = {"검사의뢰서", "의뢰서", "다운로드", "양식", "파일"}
        specific_terms = [term for term in terms if term not in generic_terms]
        combined = normalize_search_text(f"{file_name} {document_title}").replace(" ", "")
        if (
            not specific_terms
            and ("검사의뢰서" in query.replace(" ", "") or "의뢰서" in query)
            and "일반검사의뢰서" in combined
        ):
            return 60
        return 0

    @staticmethod
    def _snippet(value: str | None, limit: int = 240) -> str | None:
        text = clean_text(value)
        return text[:limit] if text else None

    @staticmethod
    def _date(value: datetime | None) -> str | None:
        return value.date().isoformat() if value else None

    @staticmethod
    def _group_values(rows: Any) -> dict[int, list[str]]:
        grouped: dict[int, list[str]] = {}
        for owner_id, value in rows:
            grouped.setdefault(owner_id, []).append(value)
        return grouped

    @staticmethod
    def _title_for(item: Any, entity_type: str) -> str:
        if entity_type == "document":
            return item.title
        if entity_type == "preservative":
            return item.test_name
        if entity_type == "route":
            return item.label
        return item.name
