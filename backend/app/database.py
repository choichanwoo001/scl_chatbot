from __future__ import annotations

from collections.abc import Iterator
from gzip import open as gzip_open
from pathlib import Path
from shutil import copyfileobj

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import DEFAULT_DATABASE_URL, ROOT, settings


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


def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or settings.database_url
    _bootstrap_default_database(url)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    pool_options = {"poolclass": StaticPool} if url == "sqlite:///:memory:" else {}
    engine = create_engine(
        url,
        future=True,
        pool_pre_ping=True,
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

    return engine


engine = create_database_engine()
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def init_database(target_engine: Engine | None = None) -> None:
    # Import registers model metadata before create_all runs.
    from . import models  # noqa: F401

    Base.metadata.create_all(target_engine or engine)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
