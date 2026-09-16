from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from .config import settings


def migration_config() -> Config:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    return config


def check_database_revision(engine: Engine, schema: str | None = None) -> None:
    expected = set(ScriptDirectory.from_config(migration_config()).get_heads())
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"version_table_schema": schema or settings.database_schema},
        )
        current = set(context.get_current_heads())
    if current != expected:
        raise RuntimeError(
            "Database schema is not current. Run Alembic upgrade head with migration credentials before starting the API."
        )
