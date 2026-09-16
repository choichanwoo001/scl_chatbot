"""Private-data-safe SQLite → PostgreSQL transfer and verification.

The source is always read-only. Imports require an empty destination and run in
one transaction; a failed import can be restarted without partial data cleanup.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    and_,
    func,
    inspect,
    select,
    text,
)

from . import models  # noqa: F401
from .database import Base

NUL_REPLACEMENT_FIELDS = {("attachment_contents", "extracted_text"), ("attachment_chunks", "text")}


class TransferError(ValueError):
    """A safe error message that never contains source field contents or a DSN."""


@contextmanager
def sqlite_source(path: Path):  # type: ignore[no-untyped-def]
    path = path.resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def backup_sqlite(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if source == destination or destination.exists():
        raise TransferError("Backup destination must be a new file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Claim the new path exclusively; never overwrite a prior backup.
    with destination.open("xb"):
        pass
    try:
        with sqlite_source(source) as origin:
            target = sqlite3.connect(destination)
            try:
                origin.backup(target)
                if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise TransferError("SQLite backup integrity check failed")
            finally:
                target.close()
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def normalize_value(column, value: Any, *, naive_utc: bool, source: bool) -> Any:  # type: ignore[no-untyped-def]
    if value is None:
        if not column.nullable and not isinstance(column.type, JSON):
            raise TransferError("required value is NULL")
        if source and not column.nullable:
            raise TransferError("required value is SQL NULL")
        return None
    kind = column.type
    if isinstance(kind, Boolean):
        if type(value) not in {bool, int} or value not in (0, 1):
            raise TransferError("invalid Boolean")
        return bool(value)
    if isinstance(kind, JSON):
        if source:
            if not isinstance(value, str):
                raise TransferError("JSON must be serialized text in SQLite")
            try:
                value = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, TypeError) as error:
                raise TransferError("invalid JSON") from error
        return value
    if isinstance(kind, DateTime):
        try:
            parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
            if not isinstance(parsed, datetime):
                raise ValueError()
            if parsed.tzinfo is None:
                if not naive_utc:
                    raise TransferError("timezone missing; explicitly confirm --assume-naive-utc")
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (TypeError, ValueError) as error:
            if isinstance(error, TransferError):
                raise
            raise TransferError("invalid timestamp") from error
    if isinstance(kind, Integer):
        bits = 64 if isinstance(kind, BigInteger) else 32
        if type(value) is not int or not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
            raise TransferError(f"invalid {bits}-bit integer")
        return value
    if isinstance(kind, Float):
        if type(value) not in {int, float} or not math.isfinite(value):
            raise TransferError("invalid finite number")
        return float(value)
    if isinstance(kind, String):
        if not isinstance(value, str) or "\x00" in value:
            raise TransferError("invalid PostgreSQL text")
        if kind.length and len(value) > kind.length:
            raise TransferError(f"text exceeds declared length {kind.length} (actual {len(value)})")
        return value
    raise TransferError("unsupported column type")


def source_tables(connection):  # type: ignore[no-untyped-def]
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    expected = set(Base.metadata.tables)
    optional = {"chat_sessions", "gemini_daily_usage"}
    if names - expected or expected - names - optional:
        raise TransferError(
            f"Unexpected source tables: extra={sorted(names - expected)}, missing={sorted(expected - names - optional)}"
        )
    return [table for table in Base.metadata.sorted_tables if table.name in names]


def source_rows(connection, table, *, naive_utc: bool, replace_text_nuls: bool = False):  # type: ignore[no-untyped-def]
    columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table.name}")')}
    if columns != set(table.columns.keys()):
        raise TransferError(f"{table.name}: source columns differ from the model")
    order = ", ".join(f'"{c.name}"' for c in table.primary_key)
    for index, row in enumerate(connection.execute(f'SELECT * FROM "{table.name}" ORDER BY {order}'), 1):
        result = {}
        for column in table.columns:
            try:
                value = row[column.name]
                if (
                    replace_text_nuls
                    and (table.name, column.name) in NUL_REPLACEMENT_FIELDS
                    and isinstance(value, str)
                ):
                    value = value.replace("\x00", "\ufffd")
                result[column.name] = normalize_value(column, value, naive_utc=naive_utc, source=True)
            except TransferError as error:
                raise TransferError(f"{table.name}.{column.name}, row #{index}: {error}") from None
        yield result


def row_bytes(row: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            row,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
            default=lambda value: value.isoformat(timespec="microseconds"),
        ).encode("utf-8")
        + b"\n"
    )


def digest_rows(rows, table):  # type: ignore[no-untyped-def]
    checksum = hashlib.sha256()
    pk_checksum = hashlib.sha256()
    count = 0
    for row in rows:
        checksum.update(row_bytes(row))
        pk_checksum.update(row_bytes({c.name: row[c.name] for c in table.primary_key}))
        count += 1
    return {"count": count, "sha256": checksum.hexdigest(), "pk_sha256": pk_checksum.hexdigest()}


def source_manifest(connection, *, naive_utc: bool = False, replace_text_nuls: bool = False):  # type: ignore[no-untyped-def]
    if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
        raise TransferError("SQLite integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise TransferError("SQLite contains foreign key violations")
    transformations = []
    if replace_text_nuls:
        for table, column in sorted(NUL_REPLACEMENT_FIELDS):
            values = connection.execute(
                f'SELECT "{column}" FROM "{table}" WHERE instr("{column}", char(0)) > 0'
            )
            count = occurrences = 0
            for row in values:
                count += 1
                occurrences += row[0].count("\x00")
            transformations.append(
                {
                    "table": table,
                    "column": column,
                    "rows": count,
                    "characters": occurrences,
                    "rule": "U+0000 -> U+FFFD",
                }
            )
    return {
        "format": 1,
        "naive_timestamps_assumed_utc": naive_utc,
        "transformations": transformations,
        "tables": {
            table.name: digest_rows(
                source_rows(connection, table, naive_utc=naive_utc, replace_text_nuls=replace_text_nuls),
                table,
            )
            for table in source_tables(connection)
        },
    }


def target_manifest(connection, tables):  # type: ignore[no-untyped-def]
    result = {}
    for table in tables:
        rows = (
            connection.execution_options(stream_results=True)
            .execute(select(table).order_by(*table.primary_key.columns))
            .mappings()
        )
        try:
            result[table.name] = digest_rows(
                (
                    {
                        column.name: normalize_value(column, row[column.name], naive_utc=False, source=False)
                        for column in table.columns
                    }
                    for row in rows
                ),
                table,
            )
        finally:
            rows.close()
            connection.execution_options(stream_results=False)
    return result


def verify_constraints(connection, schema: str) -> None:  # type: ignore[no-untyped-def]
    if connection.scalar(text("SELECT current_schema()")) != schema:
        raise TransferError("Target search_path does not match the requested application schema")
    inspector = inspect(connection)
    names = set(inspector.get_table_names(schema=schema))
    if not set(Base.metadata.tables) <= names:
        raise TransferError("Target schema is missing application tables; run migrations first")
    for table in Base.metadata.sorted_tables:
        columns = {column["name"]: column for column in inspector.get_columns(table.name, schema=schema)}
        if set(columns) != set(table.columns.keys()):
            raise TransferError(f"{table.name}: target columns differ from the model")
        if set(inspector.get_pk_constraint(table.name, schema=schema)["constrained_columns"]) != {
            c.name for c in table.primary_key
        }:
            raise TransferError(f"{table.name}: primary key differs")
        expected_fk = {
            (
                tuple(c.name for c in fk.columns),
                tuple(e.column.name for e in fk.elements),
                fk.referred_table.name,
            )
            for fk in table.foreign_key_constraints
        }
        actual_fk = {
            (tuple(fk["constrained_columns"]), tuple(fk["referred_columns"]), fk["referred_table"])
            for fk in inspector.get_foreign_keys(table.name, schema=schema)
        }
        if expected_fk != actual_fk:
            raise TransferError(f"{table.name}: foreign keys differ")
        if any(
            fk["referred_schema"] not in {None, schema}
            for fk in inspector.get_foreign_keys(table.name, schema=schema)
        ):
            raise TransferError(f"{table.name}: foreign key points outside the application schema")
        expected_unique = {
            tuple(c.name for c in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        actual_unique = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints(table.name, schema=schema)
        }
        if expected_unique != actual_unique:
            raise TransferError(f"{table.name}: unique constraints differ")
        for column in table.columns:
            if columns[column.name]["nullable"] != column.nullable:
                raise TransferError(f"{table.name}.{column.name}: nullability differs")
        for fk in table.foreign_key_constraints:
            parent = fk.referred_table.alias()
            match = [element.parent == parent.c[element.column.name] for element in fk.elements]
            orphan_count = connection.scalar(
                select(func.count())
                .select_from(table.outerjoin(parent, and_(*match)))
                .where(and_(*(c.is_not(None) for c in fk.columns)), next(iter(parent.primary_key)).is_(None))
            )
            if orphan_count:
                raise TransferError(f"{table.name}: {orphan_count} foreign key orphan rows")
    invalid = connection.execute(
        text("""
        SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname=:schema AND NOT c.convalidated
    """),
        {"schema": schema},
    ).scalar_one()
    disabled = connection.execute(
        text("""
        SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=:schema AND t.tgisinternal AND t.tgenabled NOT IN ('O','A')
    """),
        {"schema": schema},
    ).scalar_one()
    if invalid or disabled:
        raise TransferError("Target contains unvalidated constraints or disabled integrity triggers")


def reset_sequences(connection, tables, schema: str) -> None:  # type: ignore[no-untyped-def]
    for table in tables:
        for column in table.primary_key:
            if not isinstance(column.type, Integer):
                continue
            sequence = connection.execute(
                text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                {"table_name": f'"{schema}"."{table.name}"', "column_name": column.name},
            ).scalar_one()
            if sequence:
                maximum = connection.scalar(select(func.max(column)))
                connection.execute(
                    text("SELECT setval(CAST(:sequence AS regclass), :value, :called)"),
                    {
                        "sequence": sequence,
                        "value": max(1, maximum or 1),
                        "called": maximum is not None,
                    },
                )


def transfer(
    source,
    engine,
    *,
    schema: str,
    naive_utc: bool = False,
    batch_size: int = 500,
    verify_existing: bool = False,
    replace_text_nuls: bool = False,
):  # type: ignore[no-untyped-def]
    if engine.dialect.name != "postgresql":
        raise TransferError("Transfer target must be PostgreSQL")
    if batch_size < 1:
        raise TransferError("batch_size must be positive")
    manifest = source_manifest(source, naive_utc=naive_utc, replace_text_nuls=replace_text_nuls)
    tables = source_tables(source)
    with engine.begin() as connection:
        # Exclusive transfer lock plus table locks prevent overlapping imports/writers.
        connection.exec_driver_sql("SET LOCAL statement_timeout = 0")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"scl-transfer:{schema}"}
        )
        verify_constraints(connection, schema)
        locked = ", ".join(f'"{schema}"."{t.name}"' for t in Base.metadata.sorted_tables)
        connection.exec_driver_sql(f"LOCK TABLE {locked} IN EXCLUSIVE MODE")
        if verify_existing:
            if target_manifest(connection, tables) != manifest["tables"]:
                raise TransferError("Existing destination differs; no rows were modified")
            return {"status": "already_verified", **manifest}
        if any(connection.scalar(select(func.count()).select_from(t)) for t in Base.metadata.sorted_tables):
            raise TransferError(
                "Destination must be empty; use --verify-existing only to verify a completed import"
            )
        for table in tables:
            batch = []
            for row in source_rows(source, table, naive_utc=naive_utc, replace_text_nuls=replace_text_nuls):
                # JSON.NULL preserves a JSON literal null rather than a SQL NULL.
                batch.append(
                    {
                        c.name: JSON.NULL if isinstance(c.type, JSON) and row[c.name] is None else row[c.name]
                        for c in table.columns
                    }
                )
                if len(batch) >= batch_size:
                    connection.execute(table.insert(), batch)
                    batch = []
            if batch:
                connection.execute(table.insert(), batch)
        if target_manifest(connection, tables) != manifest["tables"]:
            raise TransferError("Post-import checksum mismatch; entire import rolled back")
        reset_sequences(connection, tables, schema)
    return {"status": "imported", **manifest}
