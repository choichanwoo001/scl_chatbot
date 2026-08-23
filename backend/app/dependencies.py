from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from .catalog import DatabaseCatalog
from .config import Settings
from .orchestrator import ChatOrchestrator
from .public_search import PublicDataSearch
from .repositories.taxonomy import TaxonomyRepository
from .result_provider import ResultService
from .secure_workflows import WorkflowService


@dataclass
class AppServices:
    settings: Settings
    orchestrator: ChatOrchestrator
    workflow_service: WorkflowService
    result_service: ResultService
    catalog: DatabaseCatalog
    public_search: PublicDataSearch
    taxonomy_repository: TaxonomyRepository


def get_services(request: Request) -> AppServices:
    return request.app.state.services
