from __future__ import annotations

import re
from dataclasses import dataclass

from .chat_contracts import ModelPlan

RESULT_AUTH_SUBINTENTS = {
    "navigate_to_result",
    "interpret_personal_result",
    "report_result_issue",
    "request_result_document",
    "explain_report_field",
}


@dataclass(frozen=True)
class PolicyFlags:
    requires_authentication: bool
    needs_handoff: bool
    medical_review_required: bool


class ChatPolicy:
    @staticmethod
    def is_test_followup(query: str) -> bool:
        return bool(
            re.search(
                r"(그|해당|앞의|앞에서).{0,8}(검사|항목).{0,20}(용기|검체|소요일|방법|일정|며칠)",
                query,
                re.IGNORECASE,
            )
        )

    def apply_followup(
        self,
        plan: ModelPlan,
        query: str,
        last_test_code: str | None,
        last_test_variant_key: str | None,
    ) -> None:
        if plan.domain != "test" or not last_test_code or not self.is_test_followup(query):
            return
        plan.matched_test_code = last_test_code
        plan.matched_test_variant_key = last_test_variant_key
        plan.candidate_test_codes = []
        plan.candidate_test_variant_keys = []
        plan.needs_clarification = False
        plan.requires_authentication = False
        plan.needs_handoff = False
        plan.medical_review_required = False

    @staticmethod
    def enforce(plan: ModelPlan) -> PolicyFlags:
        requires_authentication = plan.requires_authentication or (
            plan.domain == "result" and plan.sub_intent in RESULT_AUTH_SUBINTENTS
        )
        medical_review_required = plan.medical_review_required or (
            plan.domain == "result" and plan.sub_intent == "interpret_personal_result"
        )
        needs_handoff = plan.needs_handoff or medical_review_required
        plan.requires_authentication = requires_authentication
        plan.medical_review_required = medical_review_required
        plan.needs_handoff = needs_handoff
        return PolicyFlags(requires_authentication, needs_handoff, medical_review_required)
