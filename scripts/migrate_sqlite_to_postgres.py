"""Create a full SQLite backup, preflight it, then explicitly apply to an empty DB."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import settings
from app.database import create_database_engine
from app.database_transfer import TransferError, backup_sqlite, source_manifest, sqlite_source, transfer
from app.migrations import check_database_revision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--backup",
        type=Path,
        help="Create a new complete backup using the SQLite backup API and read that copy",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument(
        "--verify-existing", action="store_true", help="Check a completed import without writing data"
    )
    mode.add_argument(
        "--dry-run", action="store_true", help="Default: source checks only, no destination connection"
    )
    parser.add_argument(
        "--assume-naive-utc",
        action="store_true",
        help="Confirm that SQLite timestamps without offsets were written in UTC",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--replace-text-nuls",
        action="store_true",
        help="Explicitly replace NUL with U+FFFD in extracted attachment text/chunks only; record transformation counts",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    engine = None
    try:
        if args.backup:
            backup_sqlite(args.source, args.backup)
        with sqlite_source(args.backup or args.source) as source:
            if args.apply or args.verify_existing:
                if not settings.migration_database_url:
                    raise TransferError("MIGRATION_DATABASE_URL is required for the destination")
                if settings.database_pool_mode == "transaction":
                    raise TransferError("Bulk transfer requires a direct or session connection")
                engine = create_database_engine(settings.migration_database_url)
                check_database_revision(engine)
                report = transfer(
                    source,
                    engine,
                    schema=settings.database_schema,
                    naive_utc=args.assume_naive_utc,
                    batch_size=args.batch_size,
                    verify_existing=args.verify_existing,
                    replace_text_nuls=args.replace_text_nuls,
                )
            else:
                report = {
                    "status": "dry_run",
                    **source_manifest(
                        source, naive_utc=args.assume_naive_utc, replace_text_nuls=args.replace_text_nuls
                    ),
                }
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output + "\n", encoding="utf-8")
        print(output)
    except TransferError as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        # Driver exceptions can contain parameters, private text and credentials.
        raise SystemExit(
            f"Transfer failed ({type(error).__name__}); no source data was changed. Check connection/schema settings."
        ) from None
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
