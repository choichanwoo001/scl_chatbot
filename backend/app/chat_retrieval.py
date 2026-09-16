"""Shared retrieval policy for chat providers."""

from __future__ import annotations

from .chat_contracts import RetrievalContext
from .normalization import normalize_search_text
from .schemas import TestInfo


def public_search_types(message: str, candidates: list[TestInfo]) -> set[str]:
    """Narrow retrieval by intent cues without fabricating an answer or source.

    Test candidates are supplied to the model through the dedicated catalog
    snapshot, so the public-data query never needs to search tests a second time.
    Unknown questions deliberately retain the broad real-data search path.
    """

    query = normalize_search_text(message)
    document_cues = {
        "공문",
        "공지",
        "첨부",
        "파일",
        "양식",
        "다운로드",
        "문서",
        "리플릿",
        "뉴스",
        "건강",
        "사회공헌",
        "변경 안내",
    }
    location_cues = {"지점", "센터", "의원", "주소", "전화", "팩스", "연락처"}
    route_cues = {"메뉴", "페이지", "경로", "링크", "로그인", "비밀번호", "아이디"}
    result_cues = {"검사결과", "내 결과", "결과조회", "결과 확인", "성적서"}
    specimen_cues = {"용기", "튜브", "채취", "보관", "보존제", "24시간뇨", "차광"}

    selected: set[str] = set()
    if any(cue in query for cue in document_cues):
        selected.update({"document", "attachment", "faq"})
    if any(cue in query for cue in location_cues):
        selected.add("location")
    if any(cue in query for cue in route_cues):
        selected.add("route")
    if any(cue in query for cue in result_cues):
        selected.update({"route", "faq"})
    if any(cue in query for cue in specimen_cues):
        selected.update({"container", "preservative", "taxonomy"})
    if selected:
        return selected
    if candidates:
        return {"document", "faq", "container", "preservative", "taxonomy"}
    return {
        "document",
        "container",
        "preservative",
        "location",
        "route",
        "taxonomy",
        "faq",
        "attachment",
    }


def retrieve_context(message, catalog, public_search) -> RetrievalContext:
    candidates = catalog.search(message, limit=10)
    public_candidates = public_search.search(
        message,
        public_search_types(message, candidates),
        limit=12,
        test_candidates=candidates,
    )
    return RetrievalContext(tuple(candidates), tuple(public_candidates))
