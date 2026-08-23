from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import catalog as catalog_api
from .api import chat as chat_api
from .api import public_data as public_data_api
from .api import results as results_api
from .api import workflows as workflows_api
from .catalog import catalog
from .config import Settings, settings
from .dependencies import AppServices
from .exception_handlers import register_exception_handlers
from .orchestrator import ChatOrchestrator
from .public_search import PublicDataSearch
from .repositories.taxonomy import TaxonomyRepository
from .result_provider import ResultService
from .secure_workflows import WorkflowService


def create_services(app_settings: Settings) -> AppServices:
    search = PublicDataSearch(app_settings)
    return AppServices(
        settings=app_settings,
        orchestrator=ChatOrchestrator(app_settings, search),
        workflow_service=WorkflowService(app_settings),
        result_service=ResultService(app_settings),
        catalog=catalog,
        public_search=search,
        taxonomy_repository=TaxonomyRepository(),
    )


def create_app(app_services: AppServices | None = None) -> FastAPI:
    configured = app_services or create_services(settings)
    application = FastAPI(
        title="SCL AI 검사안내 API",
        version="0.1.0",
        description="SCL 공개 검사·문서 RDB와 OpenAI Structured Output을 사용하는 챗봇 API.",
    )
    application.state.services = configured
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(configured.settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    register_exception_handlers(application)
    application.include_router(chat_api.router)
    application.include_router(workflows_api.router)
    application.include_router(results_api.router)
    application.include_router(catalog_api.router)
    application.include_router(public_data_api.router)
    return application


services = create_services(settings)
# Backward-compatible exports for runtime probes and existing tests.
orchestrator = services.orchestrator
workflow_service = services.workflow_service
result_service = services.result_service
app = create_app(services)
