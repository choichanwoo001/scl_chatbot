from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), default="public_web", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    pages: Mapped[list[SourcePage]] = relationship(back_populates="data_source")
    tests: Mapped[list[Test]] = relationship(back_populates="data_source")


class SourcePage(Base):
    __tablename__ = "source_pages"
    __table_args__ = (UniqueConstraint("data_source_id", "url", name="uq_source_page_url"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    page_type: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    page_number: Mapped[int | None] = mapped_column(Integer)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    http_status: Mapped[int | None] = mapped_column(Integer)

    data_source: Mapped[DataSource] = relationship(back_populates="pages")


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="running", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pages_expected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_requested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_unchanged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_deactivated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text)


class Method(Base):
    __tablename__ = "methods"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    method_group: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)


class Specimen(Base):
    __tablename__ = "specimens"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    specimen_group: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)


class BillingCode(Base):
    __tablename__ = "billing_codes"
    __table_args__ = (UniqueConstraint("code_system", "code", name="uq_billing_code"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    code_system: Mapped[str] = mapped_column(String(50), default="SCL_PUBLIC", nullable=False)
    code: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Test(Base):
    __tablename__ = "tests"
    __table_args__ = (
        UniqueConstraint("data_source_id", "source_test_code", name="uq_test_source_code"),
        Index("ix_tests_normalized_name", "normalized_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    source_test_code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    missing_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    data_source: Mapped[DataSource] = relationship(back_populates="tests")
    aliases: Mapped[list[TestAlias]] = relationship(back_populates="test", cascade="all, delete-orphan")
    variants: Mapped[list[TestVariant]] = relationship(back_populates="test", cascade="all, delete-orphan")


class TestAlias(Base):
    __tablename__ = "test_aliases"
    __table_args__ = (UniqueConstraint("test_id", "normalized_alias", name="uq_test_alias"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(500), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(30), default="source_variant", nullable=False)
    source: Mapped[str] = mapped_column(String(30), default="crawl", nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    test: Mapped[Test] = relationship(back_populates="aliases")


class TestVariant(Base):
    __tablename__ = "test_variants"
    __table_args__ = (
        UniqueConstraint("test_id", "source_sample_code", name="uq_test_variant_sample"),
        Index("ix_test_variants_source_row_key", "source_row_key"),
        Index("ix_test_variants_tat", "tat_min_days", "tat_max_days"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), nullable=False)
    source_sample_code: Mapped[str] = mapped_column(String(100), nullable=False)
    source_row_key: Mapped[str] = mapped_column(String(220), nullable=False)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    method_id: Mapped[int | None] = mapped_column(ForeignKey("methods.id"))
    specimen_id: Mapped[int | None] = mapped_column(ForeignKey("specimens.id"))
    container_text: Mapped[str | None] = mapped_column(String(500))
    billing_code_text: Mapped[str | None] = mapped_column(String(500))
    schedule_text: Mapped[str | None] = mapped_column(String(300))
    schedule_days: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    schedule_shift: Mapped[str | None] = mapped_column(String(30))
    tat_text: Mapped[str | None] = mapped_column(String(200))
    tat_min_days: Mapped[float | None] = mapped_column(Float)
    tat_max_days: Mapped[float | None] = mapped_column(Float)
    detail_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_page_id: Mapped[int | None] = mapped_column(ForeignKey("source_pages.id"))
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    missing_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    test: Mapped[Test] = relationship(back_populates="variants")
    method: Mapped[Method | None] = relationship()
    specimen: Mapped[Specimen | None] = relationship()
    billing_codes: Mapped[list[TestBillingCode]] = relationship(cascade="all, delete-orphan")


class TestBillingCode(Base):
    __tablename__ = "test_billing_codes"
    __table_args__ = (UniqueConstraint("test_variant_id", "billing_code_id", name="uq_variant_billing"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    test_variant_id: Mapped[int] = mapped_column(ForeignKey("test_variants.id"), nullable=False)
    billing_code_id: Mapped[int] = mapped_column(ForeignKey("billing_codes.id"), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    billing_code: Mapped[BillingCode] = relationship()


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint("data_source_id", "external_key", "content_hash", name="uq_source_record_version"),
        Index("ix_source_records_current", "data_source_id", "external_key", "is_current"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    ingestion_run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"), nullable=False)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    source_page_id: Mapped[int] = mapped_column(ForeignKey("source_pages.id"), nullable=False)
    external_key: Mapped[str] = mapped_column(String(220), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    test_id: Mapped[int | None] = mapped_column(ForeignKey("tests.id"))
    test_variant_id: Mapped[int | None] = mapped_column(ForeignKey("test_variants.id"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TestRevision(Base):
    __tablename__ = "test_revisions"
    __table_args__ = (Index("ix_test_revisions_test_created", "test_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), nullable=False)
    test_variant_id: Mapped[int | None] = mapped_column(ForeignKey("test_variants.id"))
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    changed_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class DatasetSyncRun(Base):
    __tablename__ = "dataset_sync_runs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    dataset: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="running", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pages_requested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_unchanged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_deactivated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text)


class EntityRevision(Base):
    __tablename__ = "entity_revisions"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "content_hash", name="uq_entity_revision"),
        Index("ix_entity_revisions_entity", "entity_type", "entity_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("dataset_sync_runs.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    changed_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Container(Base):
    __tablename__ = "containers"
    __table_args__ = (
        UniqueConstraint("data_source_id", "source_external_key", name="uq_container_source_key"),
        Index("ix_containers_normalized_name", "normalized_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    source_external_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(500), nullable=False)
    additive: Mapped[str | None] = mapped_column(Text)
    major_tests_text: Mapped[str | None] = mapped_column(Text)
    collection_volume_text: Mapped[str | None] = mapped_column(Text)
    storage_text: Mapped[str | None] = mapped_column(Text)
    caution_text: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(1500))
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    missing_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ContainerAlias(Base):
    __tablename__ = "container_aliases"
    __table_args__ = (UniqueConstraint("container_id", "normalized_alias", name="uq_container_alias"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    container_id: Mapped[int] = mapped_column(ForeignKey("containers.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(500), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(30), default="source", nullable=False)


class ContainerTestMention(Base):
    __tablename__ = "container_test_mentions"
    __table_args__ = (UniqueConstraint("container_id", "normalized_mention", name="uq_container_mention"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    container_id: Mapped[int] = mapped_column(ForeignKey("containers.id"), nullable=False)
    mention_text: Mapped[str] = mapped_column(String(1000), nullable=False)
    normalized_mention: Mapped[str] = mapped_column(String(1000), nullable=False)
    test_id: Mapped[int | None] = mapped_column(ForeignKey("tests.id"))
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PublicDocument(Base):
    __tablename__ = "public_documents"
    __table_args__ = (
        UniqueConstraint("data_source_id", "source_external_key", name="uq_public_document_source_key"),
        Index("ix_public_documents_type_date", "document_type", "published_at"),
        Index("ix_public_documents_normalized_title", "normalized_title"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    source_external_key: Mapped[str] = mapped_column(String(220), nullable=False)
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    board_id: Mapped[str | None] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(1000), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    thumbnail_url: Mapped[str | None] = mapped_column(String(1500))
    source_url: Mapped[str] = mapped_column(String(1500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    missing_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class DocumentAttachment(Base):
    __tablename__ = "document_attachments"
    __table_args__ = (
        UniqueConstraint("document_id", "source_external_key", name="uq_document_attachment_source_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("public_documents.id"), nullable=False)
    source_external_key: Mapped[str] = mapped_column(String(300), nullable=False)
    file_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(50))
    download_url: Mapped[str | None] = mapped_column(String(2000))
    preview_url: Mapped[str | None] = mapped_column(String(2000))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PreservativeGuide(Base):
    __tablename__ = "preservative_guides"
    __table_args__ = (
        UniqueConstraint("data_source_id", "normalized_test_name", name="uq_preservative_test"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    normalized_test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    light_protection: Mapped[str | None] = mapped_column(String(100))
    acetic_acid_50: Mapped[str | None] = mapped_column(String(100))
    hcl_6n: Mapped[str | None] = mapped_column(String(100))
    boric_acid_10g: Mapped[str | None] = mapped_column(String(100))
    sodium_carbonate_5g: Mapped[str | None] = mapped_column(String(100))
    no_preservative: Mapped[str | None] = mapped_column(String(100))
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TaxonomyTerm(Base):
    __tablename__ = "taxonomy_terms"
    __table_args__ = (UniqueConstraint("taxonomy", "normalized_name", name="uq_taxonomy_term"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    taxonomy: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(1500))


class TaxonomyRelation(Base):
    __tablename__ = "taxonomy_relations"
    __table_args__ = (
        UniqueConstraint("parent_id", "child_id", "relation_type", name="uq_taxonomy_relation"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("taxonomy_terms.id"), nullable=False)
    child_id: Mapped[int] = mapped_column(ForeignKey("taxonomy_terms.id"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(50), default="contains", nullable=False)


class TestTaxonomyLink(Base):
    __tablename__ = "test_taxonomy_links"
    __table_args__ = (
        UniqueConstraint("test_id", "taxonomy_term_id", "relation_type", name="uq_test_taxonomy_link"),
        Index("ix_test_taxonomy_links_term", "taxonomy_term_id", "test_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), nullable=False)
    taxonomy_term_id: Mapped[int] = mapped_column(ForeignKey("taxonomy_terms.id"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(50), default="associated_with", nullable=False)
    matched_text: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ServiceLocation(Base):
    __tablename__ = "service_locations"
    __table_args__ = (
        UniqueConstraint("data_source_id", "source_external_key", name="uq_location_source_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    source_external_key: Mapped[str] = mapped_column(String(220), nullable=False)
    location_type: Mapped[str] = mapped_column(String(50), default="branch", nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    region: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(200))
    fax: Mapped[str | None] = mapped_column(String(200))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    source_url: Mapped[str] = mapped_column(String(1500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SiteRoute(Base):
    __tablename__ = "site_routes"
    __table_args__ = (UniqueConstraint("data_source_id", "path", name="uq_site_route_path"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    path: Mapped[str] = mapped_column(String(1500), nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_label: Mapped[str] = mapped_column(String(500), nullable=False)
    parent_path: Mapped[str | None] = mapped_column(String(1500))
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    menu_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_url: Mapped[str] = mapped_column(String(1500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HandoffRequest(Base):
    __tablename__ = "handoff_requests"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    session_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    inquiry_type: Mapped[str] = mapped_column(String(50), nullable=False)
    requester_name_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    phone_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    organization_encrypted: Mapped[str | None] = mapped_column(Text)
    content_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    related_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="submitted", nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ChatFeedback(Base):
    __tablename__ = "chat_feedback"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    response_id: Mapped[str | None] = mapped_column(String(120))
    session_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rating: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(80))
    comment: Mapped[str | None] = mapped_column(Text)
    redacted_question: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_question: Mapped[str] = mapped_column(Text, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(String(50))
    sub_intent: Mapped[str | None] = mapped_column(String(80))
    source_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class FAQCandidate(Base):
    __tablename__ = "faq_candidates"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    canonical_question: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_question: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_answer: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(String(50))
    sub_intent: Mapped[str | None] = mapped_column(String(80))
    source_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    positive_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    negative_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class AttachmentContent(Base):
    __tablename__ = "attachment_contents"
    __table_args__ = (UniqueConstraint("attachment_id", name="uq_attachment_content"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    attachment_id: Mapped[int] = mapped_column(ForeignKey("document_attachments.id"), nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(30), nullable=False)
    extractor: Mapped[str | None] = mapped_column(String(80))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AttachmentChunk(Base):
    __tablename__ = "attachment_chunks"
    __table_args__ = (
        UniqueConstraint("attachment_content_id", "sequence", name="uq_attachment_chunk_sequence"),
        Index("ix_attachment_chunks_content", "attachment_content_id", "sequence"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    attachment_content_id: Mapped[int] = mapped_column(ForeignKey("attachment_contents.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_label: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)


class VectorIndexItem(Base):
    __tablename__ = "vector_index_items"
    __table_args__ = (
        UniqueConstraint("vector_store_id", "local_ref", name="uq_vector_index_store_ref"),
        Index("ix_vector_index_file", "vector_store_id", "openai_file_id"),
        Index("ix_vector_index_status", "vector_store_id", "index_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    local_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(120), nullable=False)
    vector_store_id: Mapped[str] = mapped_column(String(160), nullable=False)
    openai_file_id: Mapped[str | None] = mapped_column(String(160))
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    index_status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    usage_bytes: Mapped[int | None] = mapped_column(BigInteger)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
