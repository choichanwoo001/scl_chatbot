"""Read and archive public ingestion history; keep the newest revision per entity."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select

from ..database import SessionLocal
from ..models import EntityRevision, TestRevision


class RevisionRepository:
    def __init__(self, session_factory=None):
        self.session_factory = session_factory or SessionLocal

    def list_revisions(self, kind, entity_id, limit=50):
        model = TestRevision if kind == "test" else EntityRevision
        statement = select(model)
        if kind == "test":
            statement = statement.where(model.test_id == entity_id)
        else:
            statement = statement.where(model.entity_type == kind, model.entity_id == entity_id)
        with self.session_factory() as session:
            return [
                self._record(row)
                for row in session.scalars(
                    statement.order_by(model.created_at.desc(), model.id.desc()).limit(
                        max(1, min(limit, 200))
                    )
                )
            ]

    @staticmethod
    def _record(row):
        return {column.name: getattr(row, column.name) for column in row.__table__.columns}

    @staticmethod
    def _expired(model, cutoff):
        partition = (
            [model.test_id, model.test_variant_id]
            if model is TestRevision
            else [model.entity_type, model.entity_id]
        )
        ranked = select(
            model.id,
            func.row_number()
            .over(partition_by=partition, order_by=[model.created_at.desc(), model.id.desc()])
            .label("position"),
        ).subquery()
        return select(model).where(
            model.created_at < cutoff, model.id.in_(select(ranked.c.id).where(ranked.c.position > 1))
        )

    def retention(self, days=365, *, archive: Path | None = None, apply=False):
        if days < 1:
            raise ValueError("Retention days must be positive")
        if apply and archive is None:
            raise ValueError("An archive path is required before pruning")
        cutoff = datetime.now(UTC) - timedelta(days=days)
        with self.session_factory.begin() as session:
            statements = [(model, self._expired(model, cutoff)) for model in (TestRevision, EntityRevision)]
            counts = {
                model.__tablename__: session.scalar(select(func.count()).select_from(stmt.subquery()))
                for model, stmt in statements
            }
            # Dry runs only count rows. Archiving is explicit and refuses existing files.
            if archive is not None:
                with archive.open("x", encoding="utf-8") as output:
                    for model, stmt in statements:
                        ids = []
                        for row in session.scalars(stmt).yield_per(500):
                            output.write(
                                json.dumps(
                                    {"table": model.__tablename__, "row": self._record(row)},
                                    default=str,
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                            ids.append(row.id)
                        output.flush()
                        os.fsync(output.fileno())
                        if apply:
                            for offset in range(0, len(ids), 500):
                                session.execute(delete(model).where(model.id.in_(ids[offset : offset + 500])))
            return {"retention_days": days, "eligible": counts, "applied": apply}
