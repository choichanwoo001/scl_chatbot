from datetime import UTC, datetime

import pytest
from alembic import command
from app.database import Base, create_database_engine
from app.database_transfer import TransferError, source_manifest, sqlite_source, transfer
from app.migrations import check_database_revision, migration_config
from app.models import DataSource, HandoffRequest, SourcePage
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError


def source_fixture(tmp_path):  # type: ignore[no-untyped-def]
    path = tmp_path / "source.db"
    engine = create_database_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            DataSource.__table__.insert(),
            dict(id=42, key="test", name="한국어", base_url="https://example.com", enabled=True),
        )
        connection.execute(
            SourcePage.__table__.insert(),
            dict(
                id=12,
                data_source_id=42,
                page_type="test",
                url="https://example.com/test",
                last_fetched_at=datetime(2026, 9, 13, tzinfo=UTC),
            ),
        )
        connection.execute(
            HandoffRequest.__table__.insert(),
            dict(
                id=90,
                public_id="SCL-TEST-42",
                session_hash="a" * 64,
                inquiry_type="test",
                requester_name_encrypted="ciphertext",
                phone_encrypted="ciphertext",
                content_encrypted="ciphertext",
                related_refs=["document:123"],
                consented_at=datetime(2026, 9, 13, tzinfo=UTC),
            ),
        )
    engine.dispose()
    return path


def test_initial_migration_matches_model_and_does_not_touch_other_schemas(pg) -> None:
    engine, schema, _ = pg
    check_database_revision(engine, schema)
    migration = migration_config()
    migration.attributes["schema"] = schema
    with engine.begin() as connection:
        migration.attributes["connection"] = connection
        command.check(migration)


def test_full_transfer_checksums_ids_sequences_and_verified_rerun(pg, tmp_path) -> None:
    engine, schema, _ = pg
    with sqlite_source(source_fixture(tmp_path)) as source:
        manifest = source_manifest(source, naive_utc=True)
        result = transfer(source, engine, schema=schema, naive_utc=True, batch_size=1)
        assert result["tables"] == manifest["tables"]
        assert (
            transfer(source, engine, schema=schema, naive_utc=True, verify_existing=True)["status"]
            == "already_verified"
        )
        with pytest.raises(TransferError, match="empty"):
            transfer(source, engine, schema=schema, naive_utc=True)
    with engine.begin() as connection:
        new_id = connection.execute(
            DataSource.__table__.insert()
            .values(key="next", name="다음", base_url="https://example.com")
            .returning(DataSource.id)
        ).scalar_one()
        assert new_id > 42
        refs = connection.scalar(select(HandoffRequest.related_refs))
        assert refs == ["document:123"]
        timestamp = connection.scalar(select(SourcePage.last_fetched_at))
        assert timestamp.utcoffset().total_seconds() == 0


def test_failed_import_rolls_back_every_table(pg, tmp_path, monkeypatch) -> None:
    engine, schema, _ = pg
    from app import database_transfer

    monkeypatch.setattr(database_transfer, "target_manifest", lambda *_: {})
    with sqlite_source(source_fixture(tmp_path)) as source, pytest.raises(TransferError, match="checksum"):
        transfer(source, engine, schema=schema, naive_utc=True)
    with engine.connect() as connection:
        assert all(
            connection.scalar(select(func.count()).select_from(table)) == 0
            for table in Base.metadata.sorted_tables
        )


def test_foreign_keys_are_enforced(pg) -> None:
    engine, _, _ = pg
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            SourcePage.__table__.insert().values(
                data_source_id=987654321, page_type="test", url="https://example.com"
            )
        )


def test_transaction_settings_survive_connection_reuse(pg) -> None:
    engine, schema, _ = pg
    for _ in range(3):
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == schema
            assert connection.scalar(text("SHOW statement_timeout")) == "30s"


def test_extraction_replaces_nul_before_postgres_storage(pg, monkeypatch) -> None:
    from app import attachment_ingestion
    from app.attachment_ingestion import AttachmentIngestionService, ExtractedSection, ExtractionResult
    from app.models import AttachmentChunk, DocumentAttachment, PublicDocument
    from sqlalchemy.orm import sessionmaker

    engine, schema, config = pg
    with engine.begin() as connection:
        source_id = connection.execute(
            DataSource.__table__.insert()
            .values(key="extraction", name="test", base_url="https://example.com")
            .returning(DataSource.id)
        ).scalar_one()
        document_id = connection.execute(
            PublicDocument.__table__.insert()
            .values(
                data_source_id=source_id,
                source_external_key="doc",
                document_type="notice",
                title="test",
                normalized_title="test",
                source_url="https://example.com",
                content_hash="a" * 64,
            )
            .returning(PublicDocument.id)
        ).scalar_one()
        attachment_id = connection.execute(
            DocumentAttachment.__table__.insert()
            .values(document_id=document_id, source_external_key="file", file_name="test.pdf")
            .returning(DocumentAttachment.id)
        ).scalar_one()
    monkeypatch.setattr(attachment_ingestion, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(
        attachment_ingestion, "init_database", lambda: check_database_revision(engine, schema)
    )
    service = AttachmentIngestionService(config)
    result = service._store(
        attachment_id,
        "b" * 64,
        ExtractionResult("extracted", "test", [ExtractedSection("검사\x00안내", 1, "본문\x00")]),
    )
    assert result.extracted_text == "검사\ufffd안내"
    with engine.connect() as connection:
        row = connection.execute(select(AttachmentChunk.text, AttachmentChunk.section_label)).one()
        assert row.text == "검사\ufffd안내"
        assert row.section_label == "본문\ufffd"


def test_runtime_role_can_read_write_but_cannot_create_tables(pg) -> None:
    import uuid

    from sqlalchemy.exc import ProgrammingError

    engine, schema, _ = pg
    role = "test_role_" + uuid.uuid4().hex
    # Role and grants are transaction-local test artifacts, rolled back together.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOBYPASSRLS')
            connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
            connection.exec_driver_sql(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
            connection.exec_driver_sql(f'GRANT INSERT ON "{schema}".handoff_requests TO "{role}"')
            connection.exec_driver_sql(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{role}"')
            connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
            assert connection.scalar(select(func.count()).select_from(DataSource)) == 0
            connection.execute(
                HandoffRequest.__table__.insert().values(
                    public_id="SCL-ROLE",
                    session_hash="a" * 64,
                    inquiry_type="test",
                    requester_name_encrypted="cipher",
                    phone_encrypted="cipher",
                    content_encrypted="cipher",
                    consented_at=datetime.now(UTC),
                )
            )
            with pytest.raises(ProgrammingError), connection.begin_nested():
                connection.exec_driver_sql(f'CREATE TABLE "{schema}".must_not_exist (id integer)')
        finally:
            transaction.rollback()
