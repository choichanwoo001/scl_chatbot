from dataclasses import replace

import pytest
from app.config import Settings
from app.database import create_database_engine
from app.secure_workflows import SensitiveFieldCipher


def test_production_rejects_implicit_sqlite_and_demo_seed() -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        Settings(app_environment="production")
    with pytest.raises(ValueError, match="SEED_DEMO"):
        Settings(
            app_environment="production",
            database_url="postgresql://user:pass@host/db",
            seed_demo_on_empty=True,
        )


def test_production_requires_explicit_encryption_key() -> None:
    config = Settings(
        app_environment="production",
        database_url="postgresql://user:pass@host/db",
        seed_demo_on_empty=False,
        field_encryption_key=None,
    )
    with pytest.raises(ValueError, match="FIELD_ENCRYPTION_KEY"):
        SensitiveFieldCipher(config)


@pytest.mark.parametrize("schema", ["public", "auth", "storage", "pg_catalog", 'app"; DROP SCHEMA app'])
def test_schema_cannot_target_supabase_managed_names_or_sql(schema: str) -> None:
    with pytest.raises(ValueError, match="DATABASE_SCHEMA"):
        Settings(database_schema=schema)


def test_postgres_url_normalization_and_transaction_pool_configuration() -> None:
    config = Settings(database_pool_mode="transaction")
    engine = create_database_engine("postgres://user:pass@localhost/example", app_settings=config)
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.pool.size() == config.database_pool_size
        assert engine.hide_parameters
    finally:
        engine.dispose()


def test_production_rejects_unencrypted_postgres() -> None:
    config = Settings(database_url="postgresql://user:pass@host/db", seed_demo_on_empty=False)
    config = replace(config, app_environment="production")
    with pytest.raises(ValueError, match="SSL"):
        create_database_engine("postgresql://user:pass@host/db?sslmode=disable", app_settings=config)
