from __future__ import annotations

import asyncio
import time

from .answer_coverage import apply_test_answer_coverage
from .chat_contracts import ModelPlan, ModeratedContent, RetrievalContext
from .chat_grounding import GroundedReplyBuilder
from .chat_policy import ChatPolicy
from .chat_sessions import SessionState, SessionStore
from .config import Settings
from .gemini_gateway import GeminiGateway
from .guardrails import InputInspection, inspect_input
from .offline_chat import OfflineChatResponder
from .public_search import PublicDataSearch, SearchHit, public_search
from .schemas import ChatResponse, Reply, TestInfo


class LiveChatUnavailable(RuntimeError):
    """Raised when a request forbids prepared local fallback responses."""


class ChatOrchestrator:
    def __init__(
        self,
        settings: Settings,
        search: PublicDataSearch = public_search,
        *,
        app_catalog=None,
        session_store=None,
    ) -> None:
        self.settings = settings
        self.public_search = search
        self.catalog = app_catalog or search.catalog
        self.sessions = session_store or SessionStore(
            settings.session_history_limit, settings.session_ttl_seconds, settings.session_max_entries
        )
        self.gateway = (
            GeminiGateway(settings, search, app_catalog=self.catalog) if settings.gemini_api_key else None
        )
        self.policy = ChatPolicy()
        self.reply_builder = GroundedReplyBuilder(self.catalog, search)
        self.offline_responder = OfflineChatResponder(self.catalog, search, self.reply_builder)

    def end_session(self, session_id: str) -> bool:
        return self.sessions.delete(session_id)

    async def _call_gateway(
        self,
        message: str,
        history: list[dict[str, str]],
        previous_tests: list[TestInfo] | None = None,
    ) -> tuple[
        bool,
        ModelPlan | None,
        str | None,
        RetrievalContext | None,
        dict[str, float],
    ]:
        assert self.gateway is not None
        timings: dict[str, float] = {}

        async def timed_call(name: str, function, *args, **kwargs):  # type: ignore[no-untyped-def]
            started = time.perf_counter()
            result = await asyncio.to_thread(function, *args, **kwargs)
            timings[name] = round((time.perf_counter() - started) * 1000, 1)
            return result

        retrieval = None
        try:
            interpreted = await timed_call(
                "interpretation", self.gateway.interpret, message, history, previous_tests or []
            )
            retrieval = await timed_call(
                "retrieval",
                self.gateway.retrieve_interpreted,
                message,
                interpreted,
                previous_tests or [],
            )
            plan, response_id = await timed_call("model", self.gateway.plan, message, history, retrieval)
        except ModeratedContent:
            return True, None, None, retrieval, timings
        return False, plan, response_id, retrieval, timings

    async def respond(
        self,
        message: str,
        session_id: str | None,
        *,
        require_live: bool = False,
    ) -> ChatResponse:
        response_started = time.perf_counter()
        current_session_id, state = self.sessions.get(session_id)
        inspection = inspect_input(message, self.settings.max_input_chars)

        fixed = self._guardrail_reply(inspection)
        if fixed:
            self.sessions.append(state, inspection.model_input, fixed.text)
            return self._response(current_session_id, inspection, fixed, domain="safety")

        if self.gateway:
            try:
                flagged, plan, response_id, retrieval, timings = await self._call_gateway(
                    inspection.model_input,
                    state.history,
                    state.previous_tests,
                )
            except Exception as error:
                if require_live:
                    raise LiveChatUnavailable(
                        "실시간 AI 응답을 받지 못했습니다. 준비된 답변으로 대체하지 않았습니다. 잠시 후 다시 시도해 주세요."
                    ) from error
                reply, domain = self._demo_reply(inspection.model_input, state)
                reply.text = "현재 AI 연결이 지연되어 데모 검색으로 안내합니다. " + reply.text
                self.sessions.append(state, inspection.model_input, reply.text)
                return self._response(
                    current_session_id,
                    inspection,
                    reply,
                    domain=domain,
                    force_mode="demo_fallback",
                )

            if flagged:
                reply = Reply(
                    text="안전하게 처리하기 어려운 내용이 포함되어 있어 이 요청에는 답변할 수 없습니다. 검사 안내가 필요하면 검사명이나 검체를 중심으로 다시 질문해 주세요.",
                    data_status="no_source",
                )
                self.sessions.append(state, inspection.model_input, reply.text)
                response = self._response(
                    current_session_id,
                    inspection,
                    reply,
                    domain="safety",
                    safety_action="block",
                )
                timings["total"] = round((time.perf_counter() - response_started) * 1000, 1)
                response.timings_ms = timings
                return response

            assert plan is not None
            structured_reply = getattr(retrieval, "structured_reply", None)
            if structured_reply is None:
                self.policy.apply_followup(
                    plan,
                    inspection.model_input,
                    state.last_test_code,
                    state.last_test_variant_key,
                )
            flags = self.policy.enforce(plan)
            grounding_started = time.perf_counter()
            trusted_hits = retrieval.hits_by_ref if retrieval else None
            trusted_tests = list(retrieval.test_candidates) if retrieval else None
            if (
                trusted_tests is not None
                and structured_reply is None
                and self.policy.is_test_followup(inspection.model_input)
                and state.last_test_code
            ):
                previous_test = self.catalog.get(state.last_test_code, state.last_test_variant_key)
                if previous_test and all(
                    item.variant_key != previous_test.variant_key for item in trusted_tests
                ):
                    trusted_tests.append(previous_test)
            self.reply_builder.apply_document_fallback(
                plan,
                inspection.model_input,
                trusted_hits=list(trusted_hits.values()) if trusted_hits is not None else None,
            )
            reply, matched = self._reply_from_plan(
                plan,
                trusted_hits=trusted_hits,
                trusted_tests=trusted_tests,
            )
            if structured_reply is None:
                reply = apply_test_answer_coverage(reply, plan.requested_fields, matched)
            if structured_reply is not None and not flags.requires_authentication and not flags.needs_handoff:
                reply = structured_reply
                matched = reply.test
                state.previous_tests = list(retrieval.test_candidates)
                state.last_test_code = matched.code if matched else None
                state.last_test_variant_key = matched.variant_key if matched else None
            timings["grounding"] = round((time.perf_counter() - grounding_started) * 1000, 1)
            if matched:
                state.last_test_code = matched.code
                state.last_test_variant_key = matched.variant_key
                if structured_reply is None:
                    state.previous_tests = [matched]
            if inspection.category == "profanity_with_intent":
                reply.text = "표현은 조금만 부드럽게 부탁드려요. " + reply.text
            self.sessions.append(state, inspection.model_input, reply.text)
            response = self._response(
                current_session_id,
                inspection,
                reply,
                domain=plan.domain,
                sub_intent=plan.sub_intent,
                requested_action=plan.requested_action,
                requires_authentication=flags.requires_authentication,
                needs_handoff=flags.needs_handoff,
                medical_review_required=flags.medical_review_required,
                safety_action="handoff" if flags.needs_handoff else None,
                response_id=response_id,
            )
            timings["total"] = round((time.perf_counter() - response_started) * 1000, 1)
            response.timings_ms = timings
            return response
        if require_live:
            raise LiveChatUnavailable(
                "AI API가 연결되지 않아 실시간 답변을 생성할 수 없습니다. 준비된 답변으로 대체하지 않았습니다."
            )

        reply, domain = self._demo_reply(inspection.model_input, state)
        if inspection.category == "profanity_with_intent":
            reply.text = "표현은 조금만 부드럽게 부탁드려요. " + reply.text
        self.sessions.append(state, inspection.model_input, reply.text)
        return self._response(current_session_id, inspection, reply, domain=domain)

    def _guardrail_reply(self, inspection: InputInspection) -> Reply | None:
        if inspection.category == "prompt_injection":
            return Reply(
                text="내부 지침이나 시스템 정보는 제공할 수 없습니다. 검사 및 SCL 이용 문의는 도와드릴 수 있어요."
            )
        if inspection.category == "personal_data":
            return Reply(
                text="개인정보가 포함되어 일부를 가렸습니다. 공개 챗봇에는 주민등록번호, 전화번호, 이메일 또는 개인 검사결과 원문을 입력하지 마세요. 인증이 필요한 문의는 상담 채널을 이용해 주세요."
            )
        if inspection.category == "repeated_input":
            return Reply(
                text="같은 문자가 지나치게 반복되어 요청을 처리하지 않았습니다. 질문을 짧게 다시 입력해 주세요."
            )
        if inspection.category == "profanity_only":
            return Reply(
                text="해당 표현에는 답변하기 어렵습니다. 검사명, 검사코드, 검체 또는 소요일을 입력해 주세요."
            )
        return None

    def _reply_from_plan(
        self,
        plan: ModelPlan,
        *,
        trusted_hits: dict[str, SearchHit] | None = None,
        trusted_tests: list[TestInfo] | None = None,
    ) -> tuple[Reply, TestInfo | None]:
        return self.reply_builder.build(
            plan,
            trusted_hits=trusted_hits,
            trusted_tests=trusted_tests,
        )

    def _demo_reply(self, query: str, state: SessionState) -> tuple[Reply, str]:
        result = self.offline_responder.respond(
            query,
            state.last_test_code,
            state.last_test_variant_key,
        )
        if result.matched_test:
            state.last_test_code = result.matched_test.code
            state.last_test_variant_key = result.matched_test.variant_key
        return result.reply, result.domain

    def _response(
        self,
        session_id: str,
        inspection: InputInspection,
        reply: Reply,
        domain: str,
        safety_action: str | None = None,
        response_id: str | None = None,
        force_mode: str | None = None,
        sub_intent: str | None = None,
        requested_action: str | None = None,
        requires_authentication: bool = False,
        needs_handoff: bool = False,
        medical_review_required: bool = False,
    ) -> ChatResponse:
        return ChatResponse(
            session_id=session_id,
            displayed_input=inspection.displayed_input,
            reply=reply,
            mode=force_mode or self.settings.mode,
            safety_action=safety_action or inspection.action,
            domain=domain,
            sub_intent=sub_intent,
            requested_action=requested_action,
            requires_authentication=requires_authentication,
            needs_handoff=needs_handoff,
            medical_review_required=medical_review_required,
            response_id=response_id,
        )
