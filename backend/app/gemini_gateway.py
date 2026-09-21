from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from .chat_contracts import (
    SYSTEM_INSTRUCTIONS,
    ModelPlan,
    ModeratedContent,
    RetrievalContext,
)
from .chat_retrieval import retrieve_context
from .config import Settings
from .public_search import PublicDataSearch, public_search
from .query_interpretation import (
    INTERPRET_INSTRUCTIONS,
    QueryInterpretation,
    code_candidates,
    execute_query,
    normalize_interpretation,
    query_reply,
    validate_query,
)
from .schemas import Reply, TestInfo


@dataclass(frozen=True)
class InterpretedRetrieval(RetrievalContext):
    query: QueryInterpretation
    structured_reply: Reply | None
    interpretation_response_id: str | None


class GeminiGateway:
    """Gemini implementation of the same structured planning contract."""

    uses_integrated_moderation = False

    def __init__(
        self, settings: Settings, search: PublicDataSearch = public_search, *, app_catalog=None
    ) -> None:
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini mode")
        self.settings = settings
        self.public_search = search
        self.catalog = app_catalog or search.catalog
        self.client = httpx.Client(timeout=settings.request_timeout_seconds)

    def moderate(self, text: str) -> bool:
        # Input guardrails run before this gateway. Gemini applies its configured
        # safety filters to both prompt and output during generateContent.
        return False

    def retrieve(self, message: str) -> RetrievalContext:
        return retrieve_context(message, self.catalog, self.public_search)

    def interpret(
        self, message: str, history: list[dict[str, str]], previous: list[TestInfo]
    ) -> tuple[QueryInterpretation, str | None]:
        context = json.dumps(
            {
                "question": message,
                "code_candidates": sorted(code_candidates(message)),
                "history": history[-self.settings.session_history_limit :],
                "previous_results": [
                    {
                        "code": item.code,
                        "variant_key": item.variant_key,
                        "name": item.name,
                        "specimen": item.specimen,
                    }
                    for item in previous
                ],
            },
            ensure_ascii=False,
        )
        query, response_id = self._generate(
            QueryInterpretation, INTERPRET_INSTRUCTIONS, [{"role": "user", "parts": [{"text": context}]}]
        )
        return normalize_interpretation(query, message, previous), response_id

    def retrieve_interpreted(
        self,
        message: str,
        interpreted: tuple[QueryInterpretation, str | None],
        previous: list[TestInfo],
    ) -> InterpretedRetrieval:
        query, response_id = interpreted
        if query.intent == "other":
            retrieval = self.retrieve(message)
            return InterpretedRetrieval(
                retrieval.test_candidates, retrieval.public_candidates, query, None, response_id
            )
        error = validate_query(query, message, previous)
        items, exhaustive = ([], True) if error else execute_query(self.catalog, query, previous)
        reply = query_reply(query, items, exhaustive, error)
        # The session remembers only results actually shown, so '그중' is unambiguous.
        return InterpretedRetrieval(tuple(items[:20]), (), query, reply, response_id)

    def plan(
        self,
        message: str,
        history: list[dict[str, str]],
        retrieval: RetrievalContext | None = None,
        *,
        integrated_moderation: bool = False,
    ) -> tuple[ModelPlan, str | None]:
        del integrated_moderation
        resolved = retrieval or self.retrieve_interpreted(message, self.interpret(message, history, []), [])
        if isinstance(resolved, InterpretedRetrieval) and resolved.structured_reply is not None:
            return ModelPlan(
                domain="test",
                sub_intent="search_by_name_or_code",
                requested_action="search",
                answer=resolved.structured_reply.text,
                requested_fields=resolved.query.requested_fields,
            ), resolved.interpretation_response_id
        candidates = list(resolved.test_candidates)
        public_candidates = list(resolved.public_candidates)
        grounding = (
            "아래는 사용자 질문으로 로컬 데이터베이스와 벡터 검색에서 찾은 신뢰 가능한 후보다. "
            "후보에 없는 코드, ID, URL 또는 상세값을 만들지 않는다.\n\n"
            "검사 후보:\n"
            + self.catalog.prompt_snapshot(candidates)
            + "\n\n공개 데이터 후보:\n"
            + self.public_search.prompt_snapshot(public_candidates)
        )
        contents = [
            {
                "role": "model" if item.get("role") == "assistant" else "user",
                "parts": [{"text": str(item.get("content") or "")}],
            }
            for item in history[-self.settings.session_history_limit :]
        ]
        contents.append(
            {
                "role": "user",
                "parts": [{"text": f"{grounding}\n\n사용자 질문: {message}"}],
            }
        )
        return self._generate(ModelPlan, SYSTEM_INSTRUCTIONS, contents)

    def _generate(self, result_type, instructions: str, contents: list[dict]):
        schema = result_type.model_json_schema()
        schema.pop("title", None)
        payload = {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": contents,
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "maxOutputTokens": 1400,
                "temperature": 0.1,
            },
        }
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.settings.gemini_model}:generateContent"
        )
        correction = ""
        for attempt in range(2):
            if correction:
                payload["contents"][-1]["parts"][0]["text"] += correction
            response = self.client.post(
                endpoint,
                headers={"x-goog-api-key": self.settings.gemini_api_key},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            if body.get("promptFeedback", {}).get("blockReason"):
                raise ModeratedContent("Gemini safety filter blocked the prompt")
            candidates_payload = body.get("candidates") or []
            if not candidates_payload:
                raise RuntimeError("Gemini response did not contain a candidate")
            candidate = candidates_payload[0]
            if candidate.get("finishReason") in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT"}:
                raise ModeratedContent("Gemini safety filter blocked the response")
            text = "".join(
                str(part.get("text") or "") for part in candidate.get("content", {}).get("parts", [])
            )
            try:
                plan = result_type.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError):
                if attempt:
                    raise
                correction = "\n\n필드 값과 조합을 확인하고 제공된 JSON 스키마를 정확히 지켜 다시 답하라."
                continue
            return plan, str(body.get("responseId") or f"gemini-{uuid.uuid4().hex}")
        raise RuntimeError("Gemini structured result validation failed")
