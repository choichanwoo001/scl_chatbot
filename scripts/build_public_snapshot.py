from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DB = ROOT / "data" / "scl_catalog.db"
SNAPSHOT_GZ = ROOT / "data" / "scl_catalog.snapshot.db.gz"
PRIVATE_TABLES = ("handoff_requests", "chat_feedback", "faq_candidates")


def main() -> None:
    if not RUNTIME_DB.is_file():
        raise SystemExit(f"Runtime database not found: {RUNTIME_DB}")

    backup_db = ROOT / "tmp" / "scl_catalog.public-snapshot.db"
    compressed_tmp = ROOT / "tmp" / "scl_catalog.public-snapshot.db.gz"
    backup_db.parent.mkdir(parents=True, exist_ok=True)
    backup_db.unlink(missing_ok=True)
    compressed_tmp.unlink(missing_ok=True)

    try:
        with (
            closing(sqlite3.connect(RUNTIME_DB)) as source,
            closing(sqlite3.connect(backup_db)) as target,
        ):
            with target:
                source.backup(target)
                tables = {
                    row[0] for row in target.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                for table in PRIVATE_TABLES:
                    if table in tables:
                        target.execute(f'DELETE FROM "{table}"')
            target.execute("VACUUM")
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"Snapshot integrity check failed: {integrity}")
            for table in PRIVATE_TABLES:
                if table in tables:
                    count = target.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                    if count:
                        raise RuntimeError(f"Private table was not cleared: {table}={count}")

        with backup_db.open("rb") as source, gzip.open(compressed_tmp, "wb", compresslevel=9) as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        os.replace(compressed_tmp, SNAPSHOT_GZ)
        print(f"snapshot={SNAPSHOT_GZ}")
        print(f"compressed_bytes={SNAPSHOT_GZ.stat().st_size}")
    finally:
        backup_db.unlink(missing_ok=True)
        compressed_tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
