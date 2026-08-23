import gzip
from pathlib import Path

from app import database


def test_bootstrap_default_database_copies_snapshot(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    snapshot = data_dir / "scl_catalog.snapshot.db.gz"
    with gzip.open(snapshot, "wb") as output:
        output.write(b"versioned public data")
    default_url = f"sqlite:///{(data_dir / 'scl_catalog.db').as_posix()}"

    monkeypatch.setattr(database, "ROOT", tmp_path)
    monkeypatch.setattr(database, "DEFAULT_DATABASE_URL", default_url)

    database._bootstrap_default_database(default_url)

    runtime = data_dir / "scl_catalog.db"
    assert runtime.read_bytes() == b"versioned public data"


def test_bootstrap_default_database_does_not_overwrite_runtime(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with gzip.open(data_dir / "scl_catalog.snapshot.db.gz", "wb") as output:
        output.write(b"snapshot")
    runtime = data_dir / "scl_catalog.db"
    runtime.write_bytes(b"existing runtime")
    default_url = f"sqlite:///{runtime.as_posix()}"

    monkeypatch.setattr(database, "ROOT", tmp_path)
    monkeypatch.setattr(database, "DEFAULT_DATABASE_URL", default_url)

    database._bootstrap_default_database(default_url)

    assert runtime.read_bytes() == b"existing runtime"
