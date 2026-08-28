from fastapi import APIRouter, Depends, Response

from ..dependencies import AppServices, get_services
from ..schemas import ChatRequest, ChatResponse, HealthResponse
from ..vector_index import vector_index_status

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(services: AppServices = Depends(get_services)) -> HealthResponse:
    settings = services.settings
    index_status = vector_index_status(settings.openai_vector_store_id, settings)
    index_counts = index_status.get("counts", {})
    return HealthResponse(
        mode=settings.mode,
        model=settings.chat_model,
        rag_enabled=settings.vector_search_configured,
        live_chat_available=settings.live_chat_available,
        result_provider=services.result_service.provider.name,
        vector_search_enabled=settings.vector_search_enabled,
        vector_search_configured=settings.vector_search_configured,
        vector_search_shadow_mode=settings.vector_search_shadow_mode,
        vector_index_completed=index_counts.get("completed", 0),
        vector_index_failed=index_counts.get("failed", 0),
        vector_index_items_with_errors=index_status.get("items_with_errors", 0),
        vector_index_last_synced_at=index_status.get("last_indexed_at"),
        external_web_search_enabled=settings.external_web_search_enabled,
        external_web_search_configured=settings.external_web_search_configured,
    )


@router.post("/api/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    response: Response,
    services: AppServices = Depends(get_services),
) -> ChatResponse:
    result = await services.orchestrator.respond(
        request.message,
        request.session_id,
        require_live=request.require_live,
    )
    if result.timings_ms:
        response.headers["Server-Timing"] = ", ".join(
            f"{name};dur={duration:.1f}" for name, duration in result.timings_ms.items()
        )
    return result


@router.delete("/api/sessions/{session_id}", status_code=204)
def end_chat_session(
    session_id: str,
    services: AppServices = Depends(get_services),
) -> None:
    services.result_service.clear(session_id)
    services.orchestrator.end_session(session_id)
