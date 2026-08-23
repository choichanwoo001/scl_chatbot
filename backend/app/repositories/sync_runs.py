from __future__ import annotations

from sqlalchemy import select

from ..database import SessionLocal, init_database
from ..models import DatasetSyncRun, DataSource, utcnow


class SyncRunRepository:
    def __init__(self, source_key: str, source_name: str, base_url: str) -> None:
        self.source_key = source_key
        self.source_name = source_name
        self.base_url = base_url

    def start(self, dataset: str) -> int:
        init_database()
        with SessionLocal.begin() as session:
            source = session.scalar(select(DataSource).where(DataSource.key == self.source_key))
            if source is None:
                source = DataSource(
                    key=self.source_key,
                    name=self.source_name,
                    base_url=self.base_url,
                )
                session.add(source)
                session.flush()
            run = DatasetSyncRun(data_source_id=source.id, dataset=dataset, status="running")
            session.add(run)
            session.flush()
            return run.id

    @staticmethod
    def fail(run_id: int, error: Exception) -> None:
        with SessionLocal.begin() as session:
            run = session.get(DatasetSyncRun, run_id)
            if run:
                run.status = "failed"
                run.finished_at = utcnow()
                run.error_summary = str(error)[:4000]
