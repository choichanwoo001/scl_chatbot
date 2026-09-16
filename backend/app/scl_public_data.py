from __future__ import annotations

import httpx

from .config import Settings
from .public_data_catalog import PublicCatalogSync
from .public_data_common import SyncResult as SyncResult
from .public_data_common import canonical_content_url as canonical_content_url
from .public_data_common import stable_hash as stable_hash
from .public_data_documents import PublicDocumentSync
from .public_data_storage import PublicDataStorage
from .public_data_transport import PublicDataTransport
from .repositories.sync_runs import SyncRunRepository
from .scl_crawler import SCL_BASE_URL, SCL_SOURCE_KEY


class SCLPublicDataSync(PublicDataStorage, PublicDataTransport, PublicDocumentSync, PublicCatalogSync):
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client
        self._runs = SyncRunRepository(SCL_SOURCE_KEY, "SCL 공개 홈페이지", SCL_BASE_URL)

    def _start_run(self, dataset: str) -> int:
        return self._runs.start(dataset)

    def _fail_run(self, run_id: int, error: Exception) -> None:
        self._runs.fail(run_id, error)
