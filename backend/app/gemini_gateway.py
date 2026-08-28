from __future__ import annotations

import json
import uuid

import httpx
from pydantic import ValidationError

from .catalog import catalog
from .config import Settings
from .openai_gateway import (
    SYSTEM_INSTRUCTIONS,
    ModelPlan,
    ModeratedContent,
    OpenAIGateway,
    RetrievalContext,
)
from .public_search import PublicDataSearch, public_search


class GeminiGateway:
    """Gemini implementation of the same structured planning contract."""

    uses_integrated_moderation = False

    def __init__(self, settings: Settings, search: PublicDataSearch = public_search) -> None:
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini mode")
        self.settings = settings
        self.public_search = search
        self.client = httpx.Client(timeout=settings.request_timeout_seconds)

    def moderate(self, text: str) -> bool:
        # Input guardrails run before this gateway. Gemini applies its configured
        # safety filters to both prompt and output during generateContent.
        return False

    def retrieve(self, message: str) -> RetrievalContext:
        candidates = catalog.search(message, limit=10)
        public_types = OpenAIGateway._public_search_types(message, candidates)
        public_candidates = self.public_search.search(
            message,
            public_types,
            limit=12,
            test_candidates=candidates,
        )
        return RetrievalContext(tuple(candidates), tuple(public_candidates))

    def plan(
        self,
        message: str,
        history: list[dict[str, str]],
        retrieval: RetrievalContext | None = None,
        *,
        integrated_moderation: bool = False,
    ) -> tuple[ModelPlan, str | None]:
        del integrated_moderation
        resolved = retrieval or self.retrieve(message)
        candidates = list(resolved.test_candidates)
        public_candidates = list(resolved.public_candidates)
        grounding = (
            "아래는 사용자 질문으로 로컬 데이터베이스와 벡터 검색에서 찾은 신뢰 가능한 후보다. "
            "후보에 없는 코드, ID, URL 또는 상세값을 만들지 않는다.\n\n"
            "검사 후보:\n"
            + catalog.prompt_snapshot(candidates)
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
        schema = ModelPlan.model_json_schema()
        schema.pop("title", None)
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTIONS}]},
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
                str(part.get("text") or "")
                for part in candidate.get("content", {}).get("parts", [])
            )
            try:
                plan = ModelPlan.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError):
                if attempt:
                    raise
                correction = (
                    "\n\ndomain과 sub_intent 조합을 다시 확인하고 JSON 스키마를 정확히 지켜 다시 답하라."
                )
                continue
            return plan, str(body.get("responseId") or f"gemini-{uuid.uuid4().hex}")
        raise RuntimeError("Gemini structured result validation failed")
