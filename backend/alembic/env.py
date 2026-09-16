from __future__ import annotations

from alembic import context
from app import models  # noqa: F401
from app.config import settings
from app.database import Base, create_database_engine
from sqlalchemy import MetaData

config = context.config
schema = config.attributes.get("schema", settings.database_schema)
target_metadata = MetaData()
for table in Base.metadata.sorted_tables:
    table.to_metadata(target_metadata, schema=schema, referred_schema_fn=lambda *_: schema)


def include_name(name, type_, parent_names):  # type: ignore[no-untyped-def]
    if type_ == "schema":
        return name == schema
    return True


def configure(connection=None):  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection,
        url="postgresql+psycopg://" if connection is None else None,
        target_metadata=target_metadata,
        version_table_schema=schema,
        include_schemas=True,
        include_name=include_name,
        compare_type=True,
        literal_binds=connection is None,
    )


def run(connection):  # type: ignore[no-untyped-def]
    if connection.dialect.name != "postgresql":
        raise RuntimeError("Alembic targets PostgreSQL only; SQLite development uses create_all")
    configure(connection)
    with context.begin_transaction():
        # Alembic creates its version table before executing the first revision.
        connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        context.run_migrations()


if context.is_offline_mode():
    configure()
    with context.begin_transaction():
        context.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    if not settings.migration_database_url:
        raise RuntimeError("MIGRATION_DATABASE_URL is required; runtime credentials are not used for DDL")
    if settings.database_pool_mode == "transaction":
        raise RuntimeError("Run migrations with DATABASE_POOL_MODE=direct or session")
    engine = create_database_engine(settings.migration_database_url)
    try:
        with engine.begin() as connection:
            run(connection)
    finally:
        engine.dispose()
