from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(default=None, max_length=80)
    # The public chatbot requires a live Structured Output call by default.
    # Offline fallback remains opt-in for deterministic local development only.
    require_live: bool = True


class Citation(BaseModel):
    title: str
    ref: str | None = None
    url: HttpUrl | None = None
    updated_at: str | None = None
    source_tier: Literal["internal_scl", "scl_live_web", "approved_external"] = "internal_scl"
    retrieved_at: str | None = None
    claim_ids: list[str] = Field(default_factory=list)


class TestInfo(BaseModel):
    code: str
    variant_key: str | None = None
    name: str
    aliases: list[str] = Field(default_factory=list)
    specimen: str
    container: str | None = None
    method: str
    schedule: str
    tat: str
    source_title: str
    source_url: HttpUrl | None = None
    updated_at: str
    demo: bool = True
    public_details: dict[str, str] = Field(default_factory=dict)


class Reply(BaseModel):
    kind: Literal["text", "test", "choices", "result_auth_form", "handoff_form"] = "text"
    text: str
    test: TestInfo | None = None
    choices: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    data_status: Literal[
        "public_document",
        "public_database",
        "demo_data",
        "scl_live_web",
        "approved_external",
        "mixed",
        "no_source",
    ] = "no_source"
    grounding_status: Literal[
        "grounded_internal",
        "grounded_external",
        "grounded_mixed",
        "abstained",
    ] = "abstained"
    answerability: Literal["full", "partial", "none"] = "none"
    claim_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_information: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    session_id: str
    displayed_input: str
    reply: Reply
    mode: Literal["openai", "gemini", "demo_fallback"]
    safety_action: Literal["allow", "warn", "redact", "block", "handoff"]
    domain: str = "unknown"
    sub_intent: str | None = None
    requested_action: str | None = None
    requires_authentication: bool = False
    needs_handoff: bool = False
    medical_review_required: bool = False
    response_id: str | None = None
    timings_ms: dict[str, float] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    mode: Literal["openai", "gemini", "demo_fallback"]
    model: str
    rag_enabled: bool
    live_chat_available: bool
    result_provider: str
    vector_search_enabled: bool = False
    vector_search_configured: bool = False
    vector_search_shadow_mode: bool = False
    vector_index_completed: int = 0
    vector_index_failed: int = 0
    vector_index_items_with_errors: int = 0
    vector_index_last_synced_at: str | None = None
    external_web_search_enabled: bool = False
    external_web_search_configured: bool = False


class CatalogStatus(BaseModel):
    source: str
    tests: int
    variants: int
    details: int = 0
    last_sync_at: str | None = None
    last_sync_status: str | None = None


class PublicSearchHit(BaseModel):
    ref: str
    entity_type: Literal[
        "test",
        "document",
        "container",
        "preservative",
        "location",
        "route",
        "taxonomy",
        "faq",
        "attachment",
    ]
    entity_id: str
    title: str
    snippet: str | None = None
    source_url: HttpUrl | None = None
    updated_at: str | None = None
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class PublicDataStatus(BaseModel):
    documents: int
    containers: int
    preservatives: int
    locations: int
    routes: int
    taxonomy_terms: int
    published_faqs: int = 0
    extracted_attachments: int = 0
    vector_search_enabled: bool = False
    vector_search_configured: bool = False
    vector_search_shadow_mode: bool = False
    vector_search_calls: int = 0
    vector_search_errors: int = 0
    last_vector_error: str | None = None
    vector_index_counts: dict[str, int] = Field(default_factory=dict)
    vector_index_completed_by_type: dict[str, int] = Field(default_factory=dict)
    vector_index_usage_bytes: int = 0
    vector_index_items_with_errors: int = 0
    vector_index_last_synced_at: str | None = None


class TaxonomyTestLinkInfo(BaseModel):
    test_id: int
    code: str
    name: str
    relation_type: str
    matched_text: str | None = None
    source: str
    verified: bool


class HandoffCreateRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    inquiry_type: Literal[
        "test_request", "specimen_shipping", "result_issue", "complaint", "business", "general"
    ]
    requester_name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=9, max_length=30)
    organization: str | None = Field(default=None, max_length=200)
    content: str = Field(min_length=2, max_length=2000)
    related_refs: list[str] = Field(default_factory=list, max_length=10)
    consent: bool


class HandoffReceipt(BaseModel):
    public_id: str
    status: Literal["submitted", "processing", "completed"]
    created_at: str


class FeedbackCreateRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    response_id: str | None = Field(default=None, max_length=120)
    rating: Literal["helpful", "not_helpful"]
    reason: str | None = Field(default=None, max_length=80)
    comment: str | None = Field(default=None, max_length=500)
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=3000)
    domain: str | None = Field(default=None, max_length=50)
    sub_intent: str | None = Field(default=None, max_length=80)
    source_refs: list[str] = Field(default_factory=list, max_length=10)


class FeedbackReceipt(BaseModel):
    feedback_id: int
    faq_candidate_id: int
    merged_occurrences: int


class ResultCredentials(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    user_id: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)
    identity_value: str = Field(min_length=4, max_length=100)


class ResultAuthResponse(BaseModel):
    authenticated: bool
    expires_at: str
    provider: str


class ResultListItem(BaseModel):
    result_id: str
    test_name: str
    requested_at: str
    status: Literal["received", "processing", "reported", "corrected", "cancelled"]


class ResultField(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=2000)
    reference: str | None = Field(default=None, max_length=1000)


class ResultDetail(BaseModel):
    result_id: str
    test_name: str
    requested_at: str
    reported_at: str | None = None
    status: Literal["received", "processing", "reported", "corrected", "cancelled"]
    fields: list[ResultField] = Field(default_factory=list, max_length=200)
    notice: str = Field(max_length=1000)
