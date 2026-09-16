import sqlite3
from datetime import UTC, datetime

import pytest
from app.database import Base, create_database_engine
from app.database_transfer import (
    TransferError,
    backup_sqlite,
    normalize_value,
    source_manifest,
    source_rows,
    sqlite_source,
)
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text


def test_backup_includes_wal_and_never_overwrites(tmp_path) -> None:
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE example (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO example VALUES (42)")
        connection.commit()
        backup_sqlite(source, backup)
    with sqlite_source(backup) as connection:
        assert connection.execute("SELECT id FROM example").fetchone()[0] == 42
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM example")
    with pytest.raises(TransferError, match="new file"):
        backup_sqlite(source, backup)


@pytest.mark.parametrize(("kind", "value"), [(Boolean(), 2), (Integer(), 2**40), (String(2), "secret")])
def test_invalid_values_fail_without_echoing_private_contents(kind, value) -> None:
    with pytest.raises(TransferError) as error:
        normalize_value(Column("value", kind), value, naive_utc=False, source=True)
    assert "secret" not in str(error.value)


def test_timestamp_requires_explicit_utc_assumption() -> None:
    column = Column("at", DateTime(timezone=True))
    with pytest.raises(TransferError, match="timezone missing"):
        normalize_value(column, "2026-09-13 00:00:00", naive_utc=False, source=True)
    assert normalize_value(column, "2026-09-13 00:00:00", naive_utc=True, source=True) == datetime(
        2026, 9, 13, tzinfo=UTC
    )


def test_empty_source_manifest_has_all_tables(tmp_path) -> None:
    source = tmp_path / "source.db"
    engine = create_database_engine(f"sqlite:///{source.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()
    with sqlite_source(source) as connection:
        manifest = source_manifest(connection)
    assert set(manifest["tables"]) == set(Base.metadata.tables)
    assert all(value["count"] == 0 for value in manifest["tables"].values())


def test_nul_replacement_is_explicit_and_limited_to_extracted_text() -> None:
    table = Table(
        "attachment_chunks", MetaData(), Column("id", Integer, primary_key=True), Column("text", Text)
    )
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE attachment_chunks (id INTEGER PRIMARY KEY, text TEXT)")
        connection.execute("INSERT INTO attachment_chunks VALUES (1, ?)", ("검사\x00안내",))
        with pytest.raises(TransferError, match="invalid PostgreSQL text"):
            list(source_rows(connection, table, naive_utc=False))
        rows = list(source_rows(connection, table, naive_utc=False, replace_text_nuls=True))
        assert rows == [{"id": 1, "text": "검사\ufffd안내"}]
        assert connection.execute("SELECT text FROM attachment_chunks").fetchone()[0] == "검사\x00안내"
