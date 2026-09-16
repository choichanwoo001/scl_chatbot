"""Read-only SQL export helpers shared by the offline artifact builders."""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import text

from .database import engine


class ExportConnection:
    def __init__(self, connection):  # type: ignore[no-untyped-def]
        self.connection = connection

    def execute(self, sql: str):  # type: ignore[no-untyped-def]
        return self.connection.execute(text(sql)).mappings()


@contextmanager
def export_connection():  # type: ignore[no-untyped-def]
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            if engine.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            yield ExportConnection(connection)
