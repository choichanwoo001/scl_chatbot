from __future__ import annotations

from .catalog import DatabaseCatalog
from .guardrails import safe_citation_url
from .openai_gateway import ModelPlan
from .public_search import PublicDataSearch, SearchHit
from .schemas import Citation, Reply, TestInfo


class GroundedReplyBuilder:
    """Resolves model-authored references against RDB data before replying."""

    def __init__(self, catalog: DatabaseCatalog, public_search: PublicDataSearch) -> None:
        self.catalog = catalog
        self.public_search = public_search

    def build(self, plan: ModelPlan) -> tuple[Reply, TestInfo | None]:
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

        matched = (
            self.catalog.get(plan.matched_test_code, plan.matched_test_variant_key)
            if plan.domain == "test"
            else None
        )
        if matched:
            return self.test_reply(matched, plan.answer), matched

        selected_hits = self._selected_hits(plan)
        if selected_hits:
            citations = [self.citation_from_hit(hit) for hit in selected_hits]
            return Reply(
                text=plan.answer,
                citations=[citation for citation in citations if citation],
                data_status="public_document"
                if all(hit.entity_type in {"document", "attachment", "faq"} for hit in selected_hits)
                else "public_database",
            ), None

        if self._claimed_reference(plan):
            return Reply(
                text="확인된 공개 데이터에서 해당 근거를 찾지 못했습니다. 검색 조건을 바꿔 다시 질문해 주세요.",
                data_status="no_source",
            ), None

        if plan.needs_clarification:
            return self._clarification_reply(plan), None

        return Reply(text=plan.answer, data_status="no_source"), None

    def apply_document_fallback(self, plan: ModelPlan, query: str) -> None:
        if (
            plan.domain != "document"
            or plan.matched_document_ids
            or plan.matched_content_id
            or plan.target_route
        ):
            return
        hits = self.public_search.search(query, {"document", "attachment", "faq"}, 3)
        if not hits:
            return
        top = hits[0]
        plan.matched_content_id = top.ref
        plan.answer = f"관련 공개 문서를 찾았습니다. ‘{top.title}’에서 세부 내용을 확인해 주세요."

    def _selected_hits(self, plan: ModelPlan) -> list[SearchHit]:
        selected: list[SearchHit] = []
        raw_refs = [
            *plan.matched_document_ids,
            *([plan.matched_content_id] if plan.matched_content_id else []),
        ]
        for raw_ref in raw_refs:
            ref = raw_ref if ":" in raw_ref else f"document:{raw_ref}"
            hit = self.public_search.get_ref(ref)
            if hit and all(existing.ref != hit.ref for existing in selected):
                selected.append(hit)
        if plan.target_route:
            hit = self.public_search.find_route(plan.target_route)
            if hit and all(existing.ref != hit.ref for existing in selected):
                selected.append(hit)
        return selected

    @staticmethod
    def _claimed_reference(plan: ModelPlan) -> bool:
        return bool(
            plan.matched_test_code
            or plan.matched_test_variant_key
            or plan.matched_document_ids
            or plan.matched_content_id
            or plan.target_route
        )

    def _clarification_reply(self, plan: ModelPlan) -> Reply:
        candidates: list[TestInfo] = []
        seen: set[str] = set()
        for variant_key in plan.candidate_test_variant_keys:
            item = self.catalog.get(None, variant_key)
            if item and (item.variant_key or item.code) not in seen:
                candidates.append(item)
                seen.add(item.variant_key or item.code)
        for code in plan.candidate_test_codes:
            for item in self.catalog.search(code, limit=20):
                if item.code.casefold() != code.casefold():
                    continue
                identity = item.variant_key or item.code
                if identity not in seen:
                    candidates.append(item)
                    seen.add(identity)

        if (plan.candidate_test_codes or plan.candidate_test_variant_keys) and not candidates:
            return Reply(
                text="확인된 검사 후보를 찾지 못했습니다. 검사명이나 검사코드를 다시 확인해 주세요.",
                data_status="no_source",
            )
        citations = [
            Citation(
                title=item.name,
                ref=f"test:{item.variant_key or item.code}",
                url=item.source_url,
                updated_at=item.updated_at,
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
            text = f"{top.title}: {top.snippet or '상세 내용은 출처에서 확인해 주세요.'}"
        citations = [cls.citation_from_hit(hit) for hit in hits[:3]]
        return Reply(
            text=text,
            citations=[citation for citation in citations if citation],
            data_status="public_document"
            if top.entity_type in {"document", "attachment", "faq"}
            else "public_database",
        )

    @staticmethod
    def citation_from_hit(hit: SearchHit) -> Citation | None:
        url = safe_citation_url(hit.source_url)
        if not url and hit.entity_type == "document" and hit.metadata.get("board_id"):
            url = f"https://www.scllab.co.kr/front/bbsList.do?bbsId={hit.metadata['board_id']}"
        if hit.source_url and not url:
            return None
        return Citation(title=hit.title, ref=hit.ref, url=url, updated_at=hit.updated_at)

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
                )
            ],
            data_status="demo_data" if test.demo else "public_database",
        )
