from __future__ import annotations

from openai import OpenAI
from pydantic import ValidationError

from .chat_contracts import (
    SYSTEM_INSTRUCTIONS as SYSTEM_INSTRUCTIONS,
)
from .chat_contracts import (
    ModelCitation as ModelCitation,
)
from .chat_contracts import (
    ModelClaim as ModelClaim,
)
from .chat_contracts import (
    ModelPlan as ModelPlan,
)
from .chat_contracts import (
    ModeratedContent as ModeratedContent,
)
from .chat_contracts import (
    RetrievalContext as RetrievalContext,
)
from .chat_retrieval import public_search_types, retrieve_context
from .config import Settings
from .public_search import PublicDataSearch, public_search


class OpenAIGateway:
    uses_integrated_moderation = True

    def __init__(
        self, settings: Settings, search: PublicDataSearch = public_search, *, app_catalog=None
    ) -> None:
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI mode")
        self.settings = settings
        self.public_search = search
        self.catalog = app_catalog or search.catalog
        self.client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.request_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )

    def moderate(self, text: str) -> bool:
        response = self.client.moderations.create(model="omni-moderation-latest", input=text)
        return bool(response.results and response.results[0].flagged)

    def retrieve(self, message: str) -> RetrievalContext:
        return retrieve_context(message, self.catalog, self.public_search)

    def plan(
        self,
        message: str,
        history: list[dict[str, str]],
        retrieval: RetrievalContext | None = None,
        *,
        integrated_moderation: bool = True,
    ) -> tuple[ModelPlan, str | None]:
        resolved = retrieval or self.retrieve(message)
        candidates = list(resolved.test_candidates)
        public_candidates = list(resolved.public_candidates)
        context = [
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {
                "role": "system",
                "content": "아래는 사용자 질문으로 RDB에서 먼저 검색한 검사 후보다. demo=false는 SCL 공개 홈페이지 동기화 데이터이고 demo=true만 시연 데이터다. 후보에 없는 검사코드나 상세값을 만들지 않는다.\n"
                + self.catalog.prompt_snapshot(candidates),
            },
            {
                "role": "system",
                "content": "아래는 동일 질문으로 RDB 통합 검색한 공개 데이터 후보다. 답변 근거로 선택할 때 ref를 정확히 복사한다. 후보에 없는 ID, URL, 경로를 만들지 않는다.\n"
                + self.public_search.prompt_snapshot(public_candidates),
            },
            *history,
            {"role": "user", "content": message},
        ]
        request: dict[str, object] = {
            "model": self.settings.openai_chat_model,
            "input": context,
            "text_format": ModelPlan,
            "text": {"verbosity": "low"},
            "reasoning": {"effort": self.settings.validated_reasoning_effort},
            "prompt_cache_key": "scl-chat-plan-v1",
            "store": False,
        }
        if integrated_moderation:
            request["moderation"] = {
                "model": "omni-moderation-latest",
                "policy": {
                    "input": {"mode": "score"},
                    "output": {"mode": "score"},
                },
            }
        retry_context = context
        for attempt in range(2):
            request["input"] = retry_context
            try:
                response = self.client.responses.parse(**request)
            except ValidationError:
                if attempt:
                    raise
                retry_context = [
                    *context[:-1],
                    {
                        "role": "system",
                        "content": (
                            "domain과 sub_intent 조합을 다시 확인하라. 반드시 선택한 domain에 "
                            "속한 sub_intent만 사용하고, 공개 문서 다운로드와 개인 결과 문서를 구분하라."
                        ),
                    },
                    context[-1],
                ]
                continue
            moderation = getattr(response, "moderation", None)
            if moderation is not None:
                input_result = getattr(moderation, "input", None)
                output_result = getattr(moderation, "output", None)
                if bool(getattr(input_result, "flagged", False)) or bool(
                    getattr(output_result, "flagged", False)
                ):
                    raise ModeratedContent("OpenAI integrated moderation flagged the response")
            if response.output_parsed is None:
                raise RuntimeError("OpenAI response did not contain a structured result")
            return response.output_parsed, response.id
        raise RuntimeError("OpenAI structured result validation failed")

    _public_search_types = staticmethod(public_search_types)
