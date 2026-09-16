"""Compare a frozen SQLite backup against PostgreSQL without changing data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import settings
from app.database import create_database_engine
from app.database_transfer import (
    TransferError,
    source_manifest,
    source_tables,
    sqlite_source,
    target_manifest,
    verify_constraints,
)
from app.migrations import check_database_revision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--assume-naive-utc", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--replace-text-nuls", action="store_true")
    args = parser.parse_args()
    engine = None
    try:
        if not settings.migration_database_url:
            raise TransferError("MIGRATION_DATABASE_URL is required")
        engine = create_database_engine(settings.migration_database_url)
        check_database_revision(engine)
        with sqlite_source(args.source) as source, engine.connect() as connection:
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL statement_timeout = 0")
                verify_constraints(connection, settings.database_schema)
                expected = source_manifest(
                    source, naive_utc=args.assume_naive_utc, replace_text_nuls=args.replace_text_nuls
                )
                actual = target_manifest(connection, source_tables(source))
                differences = [
                    name for name, value in expected["tables"].items() if actual.get(name) != value
                ]
                report = {
                    "ok": not differences,
                    "different_tables": differences,
                    "source": expected,
                    "target": actual,
                }
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output + "\n", encoding="utf-8")
        print(output)
        if differences:
            raise SystemExit(1)
    except TransferError as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        raise SystemExit(
            f"Verification failed ({type(error).__name__}); check connection/schema settings."
        ) from None
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
