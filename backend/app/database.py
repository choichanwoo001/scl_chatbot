from __future__ import annotations

from gzip import open as gzip_open
from pathlib import Path
from shutil import copyfileobj
from threading import Lock
from weakref import WeakSet

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import DEFAULT_DATABASE_URL, ROOT, Settings, settings


class Base(DeclarativeBase):
    pass


def _bootstrap_default_database(database_url: str) -> None:
    """Create a writable runtime DB from the versioned public-data snapshot."""

    if database_url != DEFAULT_DATABASE_URL:
        return

    runtime_path = ROOT / "data" / "scl_catalog.db"
    snapshot_path = ROOT / "data" / "scl_catalog.snapshot.db.gz"
    if runtime_path.exists() or not snapshot_path.exists():
        return

    temporary_path = Path(f"{runtime_path}.tmp")
    with gzip_open(snapshot_path, "rb") as source, temporary_path.open("wb") as destination:
        copyfileobj(source, destination, length=1024 * 1024)
    temporary_path.replace(runtime_path)


def create_database_engine(
    database_url: str | None = None,
    *,
    app_settings: Settings = settings,
) -> Engine:
    url = database_url or app_settings.database_url
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    _bootstrap_default_database(url)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    pool_options = {"poolclass": StaticPool} if url == "sqlite:///:memory:" else {}
    parsed = make_url(url)
    if parsed.get_backend_name() == "postgresql":
        sslmode = parsed.query.get("sslmode", "require")
        if app_settings.app_environment == "production" and sslmode not in {
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise ValueError("Production PostgreSQL connections require SSL")
        connect_args.update(connect_timeout=app_settings.database_connect_timeout, sslmode=sslmode)
        if app_settings.database_pool_mode == "transaction":
            connect_args["prepare_threshold"] = None
        pool_options.update(
            pool_size=app_settings.database_pool_size,
            max_overflow=app_settings.database_max_overflow,
            pool_timeout=app_settings.database_pool_timeout,
        )
    engine = create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args=connect_args,
        **pool_options,
    )

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    if parsed.get_backend_name() == "postgresql":
        # SET LOCAL is reapplied in every transaction, including transaction poolers.
        @event.listens_for(engine, "begin")
        def _configure_postgres(connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(
                f'SET LOCAL search_path TO "{app_settings.database_schema}", pg_catalog'
            )
            connection.exec_driver_sql(
                f"SET LOCAL statement_timeout = {app_settings.database_statement_timeout_ms}"
            )

    engine._scl_schema = app_settings.database_schema
    return engine


engine = create_database_engine()
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
_validated_engines: WeakSet[Engine] = WeakSet()
_validation_lock = Lock()


def init_database(target_engine: Engine | None = None) -> None:
    # Import registers model metadata before create_all runs.
    from . import models  # noqa: F401

    selected = target_engine or engine
    if selected.dialect.name == "sqlite":
        Base.metadata.create_all(selected)
        return
    # Runtime credentials never need CREATE/ALTER privileges. Migrate before startup.
    with _validation_lock:
        if selected not in _validated_engines:
            from .migrations import check_database_revision

            check_database_revision(selected, schema=getattr(selected, "_scl_schema", None))
            _validated_engines.add(selected)
