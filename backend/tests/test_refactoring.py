import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.catalog import DatabaseCatalog
from app.chat_sessions import DatabaseSessionStore, SessionStore
from app.config import Settings
from app.database import Base
from app.main import create_services
from app.models import (
    BillingCode,
    ChatSession,
    TaxonomyRelation,
    TaxonomyTerm,
)
from app.models import (
    TestBillingCode as CatalogTestBillingCode,
)
from app.models import (
    TestRevision as CatalogTestRevision,
)
from app.models import (
    TestVariant as CatalogTestVariant,
)
from app.repositories.revisions import RevisionRepository
from app.repositories.taxonomy import TaxonomyRepository
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'isolated.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def test_memory_sessions_expire_and_evict_oldest():
    now = [0.0]
    store = SessionStore(4, ttl_seconds=10, max_entries=2, clock=lambda: now[0])
    _, state = store.get("old")
    store.append(state, "first", "answer")
    store.get("second")
    store.get("third")
    assert not store.get("old")[1].history
    _, state = store.get("third")
    store.append(state, "question", "answer")
    now[0] = 11
    assert not store.get("third")[1].history


def test_database_sessions_shared_bounded_expiring_and_deletable(factory):
    first = DatabaseSessionStore(factory, 2, max_entries=2)
    second = DatabaseSessionStore(factory, 2, max_entries=2)
    _, state = first.get("shared")
    first.append(state, "question", "answer")
    assert second.get("shared")[1].history == state.history
    with factory.begin() as db:
        db.get(ChatSession, "shared").expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert not second.get("shared")[1].history
    for key in ["one", "two", "three"]:
        _, state = first.get(key)
        first.append(state, key, "reply")
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ChatSession)) == 2
    assert second.delete("three")
    assert not first.get("three")[1].history


def test_application_factories_do_not_share_catalogs_or_workflows(tmp_path):
    base = Settings(seed_demo_on_empty=False)
    first = create_services(replace(base, database_url=f"sqlite:///{(tmp_path / 'a.db').as_posix()}"))
    second = create_services(replace(base, database_url=f"sqlite:///{(tmp_path / 'b.db').as_posix()}"))
    assert first.catalog.session_factory is first.workflow_service.session_factory
    assert first.catalog.session_factory is first.public_search.session_factory
    assert first.catalog.session_factory is first.taxonomy_repository.session_factory
    assert first.orchestrator.catalog is first.catalog
    assert first.public_search.catalog is first.catalog
    assert first.catalog.session_factory.kw["bind"] is not second.catalog.session_factory.kw["bind"]
    _, state = first.orchestrator.sessions.get("same-id")
    first.orchestrator.sessions.append(state, "hello", "answer")
    assert not second.orchestrator.sessions.get("same-id")[1].history
    first.catalog.session_factory.kw["bind"].dispose()
    second.catalog.session_factory.kw["bind"].dispose()


def test_normalized_billing_links_drive_search_and_display(factory):
    catalog = DatabaseCatalog(Settings(seed_demo_on_empty=True), factory)
    with factory.begin() as db:
        variant = db.scalar(select(CatalogTestVariant))
        variant.billing_code_text = "D000000AA"
        code = BillingCode(code_system="SCL_PUBLIC", code="D123456AB")
        db.add(code)
        db.flush()
        db.add(CatalogTestBillingCode(test_variant_id=variant.id, billing_code_id=code.id))
        variant_key = variant.source_row_key
    found = catalog.search("D123456AB")
    assert any(item.variant_key == variant_key for item in found)
    assert catalog.get(None, variant_key).public_details["급여코드"] == "D123456AB"
    assert not catalog.search("D000000AA")


def test_taxonomy_relations_are_read_in_both_directions(factory):
    with factory.begin() as db:
        parent = TaxonomyTerm(taxonomy="disease_group", name="질환", normalized_name="질환")
        child = TaxonomyTerm(taxonomy="team", name="담당팀", normalized_name="담당팀")
        db.add_all([parent, child])
        db.flush()
        db.add(TaxonomyRelation(parent_id=parent.id, child_id=child.id, relation_type="handled_by"))
        parent_id, child_id = parent.id, child.id
    repo = TaxonomyRepository(factory)
    assert repo.list_relations(parent_id, 10) == repo.list_relations(child_id, 10)
    assert repo.list_relations(parent_id, 10)[0].child_name == "담당팀"


def test_revision_retention_archives_and_keeps_latest(factory, tmp_path):
    DatabaseCatalog(Settings(seed_demo_on_empty=True), factory)
    with factory.begin() as db:
        variant = db.scalar(select(CatalogTestVariant))
        test_id = variant.test_id
        for index in range(3):
            db.add(
                CatalogTestRevision(
                    test_id=test_id,
                    test_variant_id=variant.id,
                    content_hash=str(index),
                    snapshot={"version": index},
                    changed_fields={},
                    created_at=datetime.now(UTC) - timedelta(days=400 - index),
                )
            )
    repo = RevisionRepository(factory)
    assert len(repo.list_revisions("test", test_id)) == 3
    assert repo.retention()["eligible"]["test_revisions"] == 2
    with pytest.raises(ValueError):
        repo.retention(apply=True)
    archive = tmp_path / "history.jsonl"
    repo.retention(archive=archive, apply=True)
    assert len(archive.read_text(encoding="utf-8").splitlines()) == 2
    assert len(repo.list_revisions("test", test_id)) == 1
    with pytest.raises(FileExistsError):
        repo.retention(archive=archive, apply=True)


@pytest.mark.parametrize(
    "fixture",
    json.loads(
        (Path(__file__).resolve().parents[2] / "data/evals/api_contracts.json").read_text(encoding="utf-8")
    ),
    ids=lambda fixture: fixture["name"],
)
def test_shared_api_contract(fixture):
    from app.main import app
    from fastapi.testclient import TestClient

    response = TestClient(app).post("/api/chat", json=fixture["request"])
    assert response.status_code == fixture["status"]
    for path, expected in fixture["fields"].items():
        value = response.json()
        for key in path.split("."):
            value = value[key]
        assert value == expected


def test_postgres_migration_sql_includes_shared_sessions():
    from io import StringIO

    from alembic import command
    from app.migrations import migration_config

    output = StringIO()
    config = migration_config()
    config.output_buffer = output
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE app.chat_sessions" in sql
    assert "ix_chat_sessions_expires_at" in sql
    assert "20260916_0002" in sql
