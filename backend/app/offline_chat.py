from __future__ import annotations

import re
from dataclasses import dataclass

from .catalog import DatabaseCatalog
from .chat_grounding import GroundedReplyBuilder
from .public_search import PublicDataSearch
from .schemas import Reply, TestInfo


@dataclass(frozen=True)
class OfflineReply:
    reply: Reply
    domain: str
    matched_test: TestInfo | None = None


class OfflineChatResponder:
    """Explicit development-only responder; never used for live-required requests."""

    def __init__(
        self,
        catalog: DatabaseCatalog,
        public_search: PublicDataSearch,
        reply_builder: GroundedReplyBuilder,
    ) -> None:
        self.catalog = catalog
        self.public_search = public_search
        self.reply_builder = reply_builder

    def respond(
        self,
        query: str,
        last_test_code: str | None,
        last_test_variant_key: str | None,
    ) -> OfflineReply:
        if re.search(r"(내|개인|본인).{0,12}(검사)?결과|검사결과.{0,10}(조회|확인|보여)", query):
            return OfflineReply(
                Reply(
                    kind="result_auth_form",
                    text="개인 검사결과는 보안 인증 후 조회할 수 있습니다. 인증정보는 AI에 전달하거나 저장하지 않습니다.",
                ),
                "result",
            )
        if re.search(r"상담(원)?.{0,10}(연결|신청|접수)|사람.{0,8}연결", query):
            return OfflineReply(
                Reply(
                    kind="handoff_form",
                    text="아래 정보를 입력하면 다른 창으로 이동하지 않고 상담 문의를 접수할 수 있습니다.",
                ),
                "support",
            )

        is_followup = bool(
            re.search(r"(그거|그 검사|그 항목).*(용기|검체|소요일|방법|언제)", query, re.IGNORECASE)
        )
        if is_followup and last_test_code:
            test = self.catalog.get(last_test_code, last_test_variant_key)
            if test:
                return OfflineReply(
                    self.reply_builder.test_reply(test, "앞에서 확인한 검사의 정보를 다시 정리했어요."),
                    "test",
                    test,
                )

        public_hits = self.public_search.search(query, limit=4)
        if public_hits and public_hits[0].entity_type != "test":
            return OfflineReply(
                self.reply_builder.public_reply(public_hits),
                self.reply_builder.domain_for_hit(public_hits[0]),
            )

        results = self.catalog.search(query, limit=4)
        if len(results) == 1:
            return OfflineReply(
                self.reply_builder.test_reply(results[0], "요청 조건과 가장 가까운 데모 검사 항목입니다."),
                "test",
                results[0],
            )
        if len(results) > 1:
            return OfflineReply(
                Reply(
                    kind="choices",
                    text="관련 검사 후보가 여러 개예요. 확인할 항목을 선택해 주세요.",
                    choices=[f"{item.name} · {item.specimen}" for item in results],
                    data_status="demo_data" if all(item.demo for item in results) else "public_database",
                ),
                "test",
            )
        return OfflineReply(
            Reply(
                kind="choices",
                text="현재는 OpenAI API 키가 없어 제한된 데모 검색으로 동작합니다. 검사명이나 검체를 조금 더 구체적으로 알려주세요.",
                choices=["HPV 검사", "갑상선 기능 검사", "소변 마약 검사"],
            ),
            "unsupported",
        )
