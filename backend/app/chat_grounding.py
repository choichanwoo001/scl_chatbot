from __future__ import annotations

from .catalog import DatabaseCatalog
from .chat_contracts import ModelPlan
from .guardrails import safe_citation_url
from .public_search import PublicDataSearch, SearchHit
from .schemas import Citation, Reply, TestInfo

NO_SOURCE_TEXT = (
    "확인된 공개 자료에서 답변 근거를 찾지 못했습니다. "
    "확인되지 않은 내용을 추측하지 않았습니다. 검사명·검사코드나 찾으시는 문서를 더 구체적으로 알려주세요."
)


class GroundedReplyBuilder:
    """Resolves model-authored references against RDB data before replying."""

    def __init__(self, catalog: DatabaseCatalog, public_search: PublicDataSearch) -> None:
        self.catalog = catalog
        self.public_search = public_search

    def build(
        self,
        plan: ModelPlan,
        *,
        trusted_hits: dict[str, SearchHit] | None = None,
        trusted_tests: list[TestInfo] | None = None,
    ) -> tuple[Reply, TestInfo | None]:
        if plan.domain == "result" and (
            plan.requires_authentication
            or plan.sub_intent
            in {
                "navigate_to_result",
                "interpret_personal_result",
                "report_result_issue",
                "request_result_document",
                "explain_report_field",
            }
        ):
            return Reply(
                kind="result_auth_form",
                text="개인 검사결과는 보안 인증 후 조회할 수 있습니다. 인증정보는 AI에 전달하거나 저장하지 않습니다.",
                data_status="no_source",
            ), None
        if plan.needs_handoff:
            return Reply(
                kind="handoff_form",
                text="이 문의는 상담 접수가 필요합니다. 아래 정보를 입력하면 채팅 안에서 바로 접수됩니다.",
                data_status="no_source",
            ), None

        matched = self._matched_test(plan, trusted_tests)
        selected_hits = self._selected_hits(plan, trusted_hits)
        supporting_tests = self._supporting_tests(plan, trusted_tests)
        if matched:
            reply = self.test_reply(
                matched,
                "확인된 SCL 공개 검사 항목입니다. 검사 조건은 아래 공개 데이터 카드에서 확인해 주세요.",
            )
            for hit in selected_hits:
                citation = self.citation_from_hit(hit)
                if citation and all(existing.ref != citation.ref for existing in reply.citations):
                    reply.citations.append(citation)
            self._append_test_citations(reply.citations, supporting_tests)
            return reply, matched

        if selected_hits:
            reply = self.public_reply(selected_hits)
            self._append_test_citations(reply.citations, supporting_tests)
            if supporting_tests and reply.data_status == "public_document":
                reply.data_status = "mixed"
                reply.grounding_status = "grounded_mixed"
            return reply, None

        if supporting_tests:
            citations: list[Citation | None] = []
            self._append_test_citations(citations, supporting_tests)
            return Reply(
                text="질문과 관련된 SCL 공개 검사 상세 페이지를 확인했습니다. 세부 내용은 아래 출처에서 확인해 주세요.",
                citations=[citation for citation in citations if citation],
                data_status="public_database",
                grounding_status="grounded_internal",
                answerability="partial",
                claim_coverage=1.0,
                missing_information=list(plan.missing_information),
            ), None

        if self._claimed_reference(plan):
            return Reply(
                text=NO_SOURCE_TEXT,
                data_status="no_source",
                missing_information=list(plan.missing_information),
            ), None

        if plan.needs_clarification:
            return self._clarification_reply(plan, trusted_tests), None

        return Reply(
            text=NO_SOURCE_TEXT,
            data_status="no_source",
            missing_information=list(plan.missing_information),
        ), None

    def _matched_test(
        self,
        plan: ModelPlan,
        trusted_tests: list[TestInfo] | None,
    ) -> TestInfo | None:
        if plan.domain != "test" or not (plan.matched_test_code or plan.matched_test_variant_key):
            return None
        if trusted_tests is None:
            return self.catalog.get(plan.matched_test_code, plan.matched_test_variant_key)
        for item in trusted_tests:
            code_matches = not plan.matched_test_code or item.code == plan.matched_test_code
            variant_matches = (
                not plan.matched_test_variant_key or item.variant_key == plan.matched_test_variant_key
            )
            if code_matches and variant_matches:
                return item
        return None

    def apply_document_fallback(
        self,
        plan: ModelPlan,
        query: str,
        *,
        trusted_hits: list[SearchHit] | None = None,
    ) -> None:
        if (
            plan.domain != "document"
            or plan.matched_document_ids
            or plan.matched_content_id
            or plan.target_route
        ):
            return
        hits = (
            [hit for hit in trusted_hits if hit.entity_type in {"document", "attachment", "faq"}][:3]
            if trusted_hits is not None
            else self.public_search.search(query, {"document", "attachment", "faq"}, 3)
        )
        if not hits:
            return
        top = hits[0]
        plan.matched_content_id = top.ref
        plan.answer = f"관련 공개 문서를 찾았습니다. ‘{top.title}’에서 세부 내용을 확인해 주세요."

    def _selected_hits(
        self,
        plan: ModelPlan,
        trusted_hits: dict[str, SearchHit] | None = None,
    ) -> list[SearchHit]:
        selected: list[SearchHit] = []
        raw_refs = [
            *plan.matched_document_ids,
            *([plan.matched_content_id] if plan.matched_content_id else []),
        ]
        for raw_ref in raw_refs:
            ref = raw_ref if ":" in raw_ref else f"document:{raw_ref}"
            hit = trusted_hits.get(ref) if trusted_hits is not None else self.public_search.get_ref(ref)
            if hit and all(existing.ref != hit.ref for existing in selected):
                selected.append(hit)
        if plan.target_route:
            hit = (
                trusted_hits.get(plan.target_route)
                if trusted_hits is not None and plan.target_route.startswith("route:")
                else self.public_search.find_route(plan.target_route)
                if trusted_hits is None
                else None
            )
            if hit and all(existing.ref != hit.ref for existing in selected):
                selected.append(hit)
        return selected

    def _supporting_tests(
        self,
        plan: ModelPlan,
        trusted_tests: list[TestInfo] | None,
    ) -> list[TestInfo]:
        trusted_by_variant = {item.variant_key: item for item in (trusted_tests or []) if item.variant_key}
        selected: list[TestInfo] = []
        for variant_key in plan.supporting_test_variant_keys:
            item = (
                trusted_by_variant.get(variant_key)
                if trusted_tests is not None
                else self.catalog.get(None, variant_key)
            )
            if (
                item
                and item.public_details
                and all(existing.variant_key != item.variant_key for existing in selected)
            ):
                selected.append(item)
        return selected[:4]

    @staticmethod
    def _append_test_citations(
        citations: list[Citation | None],
        tests: list[TestInfo],
    ) -> None:
        existing_refs = {citation.ref for citation in citations if citation}
        for item in tests:
            ref = f"test:{item.variant_key or item.code}"
            if ref in existing_refs:
                continue
            citations.append(
                Citation(
                    title=item.name,
                    ref=ref,
                    url=item.source_url,
                    updated_at=item.updated_at,
                    source_tier="internal_scl",
                    claim_ids=["verified-test"],
                )
            )
            existing_refs.add(ref)

    @staticmethod
    def _claimed_reference(plan: ModelPlan) -> bool:
        return bool(
            plan.matched_test_code
            or plan.matched_test_variant_key
            or plan.matched_document_ids
            or plan.matched_content_id
            or plan.target_route
        )

    def _clarification_reply(
        self,
        plan: ModelPlan,
        trusted_tests: list[TestInfo] | None = None,
    ) -> Reply:
        candidates: list[TestInfo] = []
        seen: set[str] = set()
        by_variant = {
            item.variant_key: item for item in (trusted_tests or []) if item.variant_key is not None
        }
        by_code: dict[str, list[TestInfo]] = {}
        for item in trusted_tests or []:
            by_code.setdefault(item.code.casefold(), []).append(item)

        for variant_key in plan.candidate_test_variant_keys:
            item = (
                by_variant.get(variant_key)
                if trusted_tests is not None
                else self.catalog.get(None, variant_key)
            )
            if item and (item.variant_key or item.code) not in seen:
                candidates.append(item)
                seen.add(item.variant_key or item.code)
        for code in plan.candidate_test_codes:
            source = (
                by_code.get(code.casefold(), [])
                if trusted_tests is not None
                else self.catalog.search(code, limit=20)
            )
            for item in source:
                if item.code.casefold() != code.casefold():
                    continue
                identity = item.variant_key or item.code
                if identity not in seen:
                    candidates.append(item)
                    seen.add(identity)

        if (plan.candidate_test_codes or plan.candidate_test_variant_keys) and not candidates:
            return Reply(
                text=NO_SOURCE_TEXT,
                data_status="no_source",
            )
        citations = [
            Citation(
                title=item.name,
                ref=f"test:{item.variant_key or item.code}",
                url=item.source_url,
                updated_at=item.updated_at,
                source_tier="internal_scl",
                claim_ids=["verified-choice"],
            )
            for item in candidates[:4]
        ]
        names = [f"{item.name} · {item.specimen}" for item in candidates]
        return Reply(
            kind="choices",
            text=plan.clarification_question or plan.answer,
            choices=(names or plan.choices)[:4],
            citations=citations,
            data_status=(
                "demo_data"
                if candidates and all(item.demo for item in candidates)
                else "public_database"
                if candidates
                else "no_source"
            ),
            grounding_status="grounded_internal" if candidates else "abstained",
            answerability="partial" if candidates else "none",
            claim_coverage=1.0 if candidates else 0.0,
        )

    @staticmethod
    def domain_for_hit(hit: SearchHit) -> str:
        if hit.entity_type == "document":
            return (
                "document" if hit.metadata.get("document_type") == "official_notice" else "corporate_content"
            )
        if hit.entity_type in {"attachment", "faq"}:
            return str(hit.metadata.get("domain") or "document")
        if hit.entity_type in {"container", "preservative", "taxonomy"}:
            return "test"
        return "support"

    @classmethod
    def public_reply(cls, hits: list[SearchHit]) -> Reply:
        top = hits[0]
        if top.entity_type == "route":
            text = f"‘{top.title}’ 메뉴로 이동하면 됩니다. {top.snippet or ''}".strip()
        elif top.entity_type == "location":
            text = f"{top.title}: {top.snippet or '연락처 상세 페이지를 확인해 주세요.'}"
        elif top.entity_type == "container":
            values = [
                top.metadata.get("additive"),
                top.metadata.get("collection_volume"),
                top.metadata.get("storage"),
            ]
            text = f"{top.title}: " + " · ".join(str(value) for value in values if value)
        elif top.entity_type == "preservative":
            values = [f"{key}={value}" for key, value in top.metadata.items() if value]
            text = f"{top.title} 요보존제 안내: " + ", ".join(values)
        else:
            text = (
                f"관련 공개 문서를 찾았습니다. ‘{top.title}’에서 세부 내용을 확인해 주세요."
                if top.entity_type in {"document", "attachment", "faq"}
                else f"{top.title}: {top.snippet or '상세 내용은 출처에서 확인해 주세요.'}"
            )
        citations = [cls.citation_from_hit(hit) for hit in hits[:3]]
        return Reply(
            text=text,
            citations=[citation for citation in citations if citation],
            data_status="public_document"
            if top.entity_type in {"document", "attachment", "faq"}
            else "public_database",
            grounding_status="grounded_internal",
            answerability="full",
            claim_coverage=1.0,
        )

    @staticmethod
    def citation_from_hit(hit: SearchHit) -> Citation | None:
        url = safe_citation_url(hit.source_url)
        if not url and hit.entity_type == "document" and hit.metadata.get("board_id"):
            url = f"https://www.scllab.co.kr/front/bbsList.do?bbsId={hit.metadata['board_id']}"
        if hit.source_url and not url:
            return None
        return Citation(
            title=hit.title,
            ref=hit.ref,
            url=url,
            updated_at=hit.updated_at,
            source_tier="internal_scl",
            claim_ids=["verified-public-record"],
        )

    @staticmethod
    def test_reply(test: TestInfo, text: str) -> Reply:
        return Reply(
            kind="test",
            text=text,
            test=test,
            citations=[
                Citation(
                    title=test.source_title,
                    ref=f"test:{test.variant_key or test.code}",
                    url=test.source_url,
                    updated_at=test.updated_at,
                    source_tier="internal_scl",
                    claim_ids=["verified-test"],
                )
            ],
            data_status="demo_data" if test.demo else "public_database",
            grounding_status="grounded_internal",
            answerability="full",
            claim_coverage=1.0,
        )
