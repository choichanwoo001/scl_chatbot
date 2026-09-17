"""Opt-in PostgreSQL tests. Never reuse a cloud/production DB or app schema."""

import os
import uuid

import pytest

os.environ["SCL_SKIP_LOCAL_ENV"] = "true"
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SEED_DEMO_ON_EMPTY", "false")
os.environ["GEMINI_API_KEY"] = ""

from alembic import command
from app.config import Settings
from app.database import Base, create_database_engine
from app.migrations import migration_config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session")
def pg_database():  # type: ignore[no-untyped-def]
    raw = os.getenv("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("TEST_DATABASE_URL not configured; run against a dedicated local PostgreSQL test DB")
    url = make_url(raw)
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or not (url.database or "").endswith("_test")
    ):
        pytest.fail("TEST_DATABASE_URL must target a local database whose name ends in _test")
    schema = "test_" + uuid.uuid4().hex
    config = Settings(database_schema=schema, database_pool_mode="direct", seed_demo_on_empty=False)
    engine = create_database_engine(raw, app_settings=config)
    admin = create_engine(url, hide_parameters=True)
    try:
        migration = migration_config()
        migration.attributes["schema"] = schema
        with engine.begin() as connection:
            migration.attributes["connection"] = connection
            command.upgrade(migration, "head")
        yield engine, schema, config
    finally:
        engine.dispose()
        # Only the generated test schema is eligible for cleanup.
        assert schema.startswith("test_") and len(schema) == 37
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture()
def pg(pg_database):  # type: ignore[no-untyped-def]
    engine, schema, config = pg_database
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    return engine, schema, config
