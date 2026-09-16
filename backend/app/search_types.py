from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SEARCH_TYPES = {
    "test",
    "document",
    "container",
    "preservative",
    "location",
    "route",
    "taxonomy",
    "faq",
    "attachment",
}
STOP_WORDS = {
    "알려줘",
    "찾아줘",
    "보여줘",
    "안내",
    "검색",
    "관련",
    "정보",
    "어디",
    "뭐야",
    "무엇",
    "홈페이지",
    "검사",
    "검사는",
    "검사가",
    "검사를",
    "검사에",
    "검사에서",
    "검사항목",
    "어떤",
    "어느",
    "있나요",
    "있습니까",
    "인가요",
    "가능한가요",
    "가능한지",
    "되나요",
    "해주세요",
    "관련된",
    "관련한",
    "페이지",
    "메뉴",
    "링크",
    "문의",
    "내용",
    "문서",
    "다운로드",
}


@dataclass(frozen=True)
class SearchHit:
    ref: str
    entity_type: str
    entity_id: str
    title: str
    snippet: str | None
    source_url: str | None
    updated_at: str | None
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _AttachmentSearchRow:
    attachment_id: int
    content_id: int
    file_name: str
    normalized_file_name: str
    file_type: str
    download_url: str
    document_id: int
    document_title: str
    document_source_url: str
    document_normalized_title: str
    text: str
    normalized_text: str
    page_number: int | None
    section_label: str | None
    extracted_at: datetime | None


@dataclass(frozen=True)
class _DocumentSearchRow:
    document_id: int
    title: str
    normalized_title: str
    normalized_extra: str
    snippet: str | None
    source_url: str
    updated_at: datetime | None
    document_type: str
    board_id: str
