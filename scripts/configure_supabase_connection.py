"""Build a password-escaped migration DSN from local Supabase connection fields.

This only prepares MIGRATION_DATABASE_URL; it never switches the running API.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import dotenv_values, set_key
from sqlalchemy.engine import URL


def configure(path: Path) -> None:
    values = dotenv_values(path)
    project = values.get("SUPABASE_PROJECT_REF") or ""
    host = values.get("SUPABASE_DB_HOST") or ""
    password = values.get("SUPABASE_DB_PASSWORD") or ""
    if not project.isalnum() or not host.endswith(".pooler.supabase.com"):
        raise ValueError("Set SUPABASE_PROJECT_REF and the exact SUPABASE_DB_HOST from Connect → Session pooler")
    if not password:
        raise ValueError("Set SUPABASE_DB_PASSWORD in .env before preparing the connection")
    url = URL.create(
        "postgresql+psycopg", username=f"postgres.{project}", password=password,
        host=host, port=5432, database="postgres", query={"sslmode": "require"},
    )
    set_key(path, "MIGRATION_DATABASE_URL", url.render_as_string(hide_password=False))
    set_key(path, "DATABASE_SCHEMA", "app")
    set_key(path, "DATABASE_POOL_MODE", "session")
    set_key(path, "SEED_DEMO_ON_EMPTY", "false")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    args = parser.parse_args()
    try:
        configure(args.env_file)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    print("MIGRATION_DATABASE_URL prepared. Password is URL-encoded. Runtime DATABASE_URL is unchanged.")


if __name__ == "__main__":
    main()
