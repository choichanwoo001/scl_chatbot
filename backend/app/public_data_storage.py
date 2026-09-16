from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from .database import SessionLocal
from .models import (
    Container,
    ContainerAlias,
    ContainerTestMention,
    DatasetSyncRun,
    DocumentAttachment,
    EntityRevision,
    PreservativeGuide,
    PublicDocument,
    SiteRoute,
    SourcePage,
    TaxonomyTerm,
    utcnow,
)
from .normalization import changed_fields, clean_text, normalize_search_text
from .public_data_common import SyncResult, stable_hash


class PublicDataStorage:
    def _store_containers(
        self,
        run_id: int,
        pages: list[tuple[int, str, str]],
        payloads: list[dict[str, Any]],
    ) -> SyncResult:
        now = datetime.now(UTC)
        inserted = updated = unchanged = deactivated = 0
        seen: set[str] = set()
        with SessionLocal.begin() as session:
            run = session.get(DatasetSyncRun, run_id)
            if run is None:
                raise RuntimeError("Container sync run disappeared")
            source_id = run.data_source_id
            self._store_pages(session, source_id, "containers", pages, now)
            existing = {
                item.source_external_key: item
                for item in session.scalars(select(Container).where(Container.data_source_id == source_id))
            }
            for payload in payloads:
                key = str(payload["source_external_key"])
                seen.add(key)
                digest = stable_hash(payload)
                item = existing.get(key)
                old_payload: dict[str, Any] = {}
                if item is None:
                    item = Container(
                        data_source_id=source_id,
                        source_external_key=key,
                        name=str(payload["name"]),
                        normalized_name=normalize_search_text(str(payload["name"])),
                        source_url=str(payload["source_url"]),
                        content_hash=digest,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    session.add(item)
                    session.flush()
                    existing[key] = item
                    inserted += 1
                else:
                    old_payload = self._container_payload(item)
                    if item.content_hash == digest:
                        item.last_seen_at = now
                        item.status = "active"
                        item.missing_runs = 0
                        unchanged += 1
                        self._sync_container_aliases(session, item)
                        self._sync_container_mentions(session, item)
                        continue
                    updated += 1

                item.name = str(payload["name"])
                item.normalized_name = normalize_search_text(str(payload["name"]))
                item.additive = payload.get("additive")
                item.major_tests_text = payload.get("major_tests_text")
                item.collection_volume_text = payload.get("collection_volume_text")
                item.storage_text = payload.get("storage_text")
                item.caution_text = payload.get("caution_text")
                item.image_url = payload.get("image_url")
                item.source_url = str(payload["source_url"])
                item.content_hash = digest
                item.last_seen_at = now
                item.status = "active"
                item.missing_runs = 0
                session.flush()
                session.add(
                    EntityRevision(
                        sync_run_id=run.id,
                        entity_type="container",
                        entity_id=item.id,
                        content_hash=digest,
                        snapshot=payload,
                        changed_fields=changed_fields(old_payload, payload),
                    )
                )
                self._sync_container_aliases(session, item)
                self._sync_container_mentions(session, item)

            for key, item in existing.items():
                if key not in seen:
                    item.missing_runs += 1
                    if item.missing_runs >= self.settings.scl_inactive_after_misses:
                        if item.status != "inactive":
                            deactivated += 1
                        item.status = "inactive"

            self._complete_run(run, len(pages), len(payloads), inserted, updated, unchanged, deactivated)
        return SyncResult(
            "containers", run_id, len(pages), len(payloads), inserted, updated, unchanged, deactivated
        )

    def _store_documents(
        self,
        run_id: int,
        dataset: str,
        pages: list[tuple[int, str, str]],
        payloads: list[dict[str, Any]],
    ) -> SyncResult:
        now = datetime.now(UTC)
        inserted = updated = unchanged = deactivated = 0
        seen: set[str] = set()
        with SessionLocal.begin() as session:
            run = session.get(DatasetSyncRun, run_id)
            if run is None:
                raise RuntimeError("Document sync run disappeared")
            source_id = run.data_source_id
            self._store_pages(session, source_id, dataset, pages, now)
            document_types = {str(payload["document_type"]) for payload in payloads}
            existing = {
                item.source_external_key: item
                for item in session.scalars(
                    select(PublicDocument).where(
                        PublicDocument.data_source_id == source_id,
                        PublicDocument.document_type.in_(document_types),
                    )
                )
            }
            for raw_payload in payloads:
                attachments = list(raw_payload.get("attachments") or [])
                payload = {key: value for key, value in raw_payload.items() if key != "attachments"}
                key = str(payload["source_external_key"])
                seen.add(key)
                digest = stable_hash(payload)
                item = existing.get(key)
                old_payload: dict[str, Any] = {}
                published = self._parse_date(payload.get("published_at"))
                if item is None:
                    item = PublicDocument(
                        data_source_id=source_id,
                        source_external_key=key,
                        document_type=str(payload["document_type"]),
                        title=str(payload["title"]),
                        normalized_title=normalize_search_text(str(payload["title"])),
                        source_url=str(payload["source_url"]),
                        content_hash=digest,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    session.add(item)
                    session.flush()
                    existing[key] = item
                    inserted += 1
                else:
                    old_payload = self._document_payload(item)
                    if item.content_hash == digest:
                        item.last_seen_at = now
                        item.status = "active"
                        item.missing_runs = 0
                        unchanged += 1
                        self._sync_attachments(session, item, attachments, now)
                        continue
                    updated += 1

                item.document_type = str(payload["document_type"])
                item.board_id = payload.get("board_id")
                item.title = str(payload["title"])
                item.normalized_title = normalize_search_text(item.title)
                item.summary = payload.get("summary")
                item.body_text = payload.get("body_text")
                item.published_at = published
                item.thumbnail_url = payload.get("thumbnail_url")
                item.source_url = str(payload["source_url"])
                item.content_hash = digest
                item.last_seen_at = now
                item.status = "active"
                item.missing_runs = 0
                session.flush()
                session.add(
                    EntityRevision(
                        sync_run_id=run.id,
                        entity_type="public_document",
                        entity_id=item.id,
                        content_hash=digest,
                        snapshot=payload,
                        changed_fields=changed_fields(old_payload, payload),
                    )
                )
                self._sync_attachments(session, item, attachments, now)

            for key, item in existing.items():
                if key not in seen:
                    item.missing_runs += 1
                    if item.missing_runs >= self.settings.scl_inactive_after_misses:
                        if item.status != "inactive":
                            deactivated += 1
                        item.status = "inactive"
            self._complete_run(run, len(pages), len(payloads), inserted, updated, unchanged, deactivated)
        return SyncResult(
            dataset, run_id, len(pages), len(payloads), inserted, updated, unchanged, deactivated
        )

    @staticmethod
    def _parse_date(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return None

    @staticmethod
    def _document_payload(item: PublicDocument) -> dict[str, Any]:
        return {
            "source_external_key": item.source_external_key,
            "document_type": item.document_type,
            "board_id": item.board_id,
            "title": item.title,
            "summary": item.summary,
            "body_text": item.body_text,
            "published_at": item.published_at.date().isoformat() if item.published_at else None,
            "thumbnail_url": item.thumbnail_url,
            "source_url": item.source_url,
        }

    @staticmethod
    def _sync_attachments(
        session, document: PublicDocument, payloads: list[dict[str, Any]], now: datetime
    ) -> None:  # type: ignore[no-untyped-def]
        existing = {
            item.source_external_key: item
            for item in session.scalars(
                select(DocumentAttachment).where(DocumentAttachment.document_id == document.id)
            )
        }
        seen: set[str] = set()
        for payload in payloads:
            key = str(payload["source_external_key"])
            seen.add(key)
            item = existing.get(key)
            if item is None:
                item = DocumentAttachment(
                    document_id=document.id,
                    source_external_key=key,
                    file_name=str(payload["file_name"]),
                    first_seen_at=now,
                    last_seen_at=now,
                )
                session.add(item)
            item.file_name = str(payload["file_name"])
            item.file_type = payload.get("file_type")
            item.download_url = payload.get("download_url")
            item.preview_url = payload.get("preview_url")
            item.last_seen_at = now
            item.status = "active"
        for key, item in existing.items():
            if key not in seen:
                item.status = "inactive"

    @staticmethod
    def _container_payload(item: Container) -> dict[str, Any]:
        return {
            "source_external_key": item.source_external_key,
            "name": item.name,
            "additive": item.additive,
            "major_tests_text": item.major_tests_text,
            "collection_volume_text": item.collection_volume_text,
            "storage_text": item.storage_text,
            "caution_text": item.caution_text,
            "image_url": item.image_url,
            "source_url": item.source_url,
        }

    @staticmethod
    def _sync_container_mentions(session, container: Container) -> None:  # type: ignore[no-untyped-def]
        raw = container.major_tests_text or ""
        mentions = [clean_text(value) for value in re.split(r"[,;/\n]+", raw) if clean_text(value)]
        existing = {
            item.normalized_mention
            for item in session.scalars(
                select(ContainerTestMention).where(ContainerTestMention.container_id == container.id)
            )
        }
        for mention in mentions:
            normalized = normalize_search_text(mention)
            if normalized and normalized not in existing:
                session.add(
                    ContainerTestMention(
                        container_id=container.id,
                        mention_text=mention,
                        normalized_mention=normalized,
                    )
                )

    @staticmethod
    def _sync_container_aliases(session, container: Container) -> None:  # type: ignore[no-untyped-def]
        candidates: list[str] = []
        stripped = clean_text(re.sub(r"\([^)]*\)", " ", container.name))
        if stripped and normalize_search_text(stripped) != container.normalized_name:
            candidates.append(stripped)
        for inner in re.findall(r"\(([^)]*)\)", container.name):
            alias = clean_text(inner)
            if alias and not re.fullmatch(r"[\d.,\s-]+(?:mL|Tube)?", alias, re.I):
                candidates.extend(clean_text(part) for part in alias.split(",") if clean_text(part))
        existing = {
            item.normalized_alias
            for item in session.scalars(
                select(ContainerAlias).where(ContainerAlias.container_id == container.id)
            )
        }
        for alias in candidates:
            normalized = normalize_search_text(alias)
            if normalized and normalized not in existing and normalized != container.normalized_name:
                session.add(
                    ContainerAlias(
                        container_id=container.id,
                        alias=alias,
                        normalized_alias=normalized,
                        alias_type="source_parenthetical",
                    )
                )
                existing.add(normalized)

    def _store_simple(
        self,
        run_id: int,
        dataset: str,
        pages: list[tuple[int, str, str]],
        payloads: list[dict[str, Any]],
        model: Any,
        key_column: str,
        key_fn: Any,
        scope_column: str | None = None,
        scope_value: str | None = None,
    ) -> SyncResult:
        now = datetime.now(UTC)
        inserted = updated = unchanged = deactivated = 0
        with SessionLocal.begin() as session:
            run = session.get(DatasetSyncRun, run_id)
            if run is None:
                raise RuntimeError(f"{dataset} sync run disappeared")
            self._store_pages(session, run.data_source_id, dataset, pages, now)
            query = select(model)
            if "data_source_id" in model.__table__.columns:
                query = query.where(model.data_source_id == run.data_source_id)
            if scope_column and scope_value is not None:
                query = query.where(getattr(model, scope_column) == scope_value)
            existing = {str(getattr(item, key_column)): item for item in session.scalars(query)}
            seen: set[str] = set()
            columns = set(model.__table__.columns.keys())
            for payload in payloads:
                key = str(key_fn(payload))
                seen.add(key)
                digest = stable_hash(payload)
                item = existing.get(key)
                if item is None:
                    values = {name: value for name, value in payload.items() if name in columns}
                    if "data_source_id" in columns:
                        values["data_source_id"] = run.data_source_id
                    if "content_hash" in columns:
                        values["content_hash"] = digest
                    if "last_seen_at" in columns:
                        values["last_seen_at"] = now
                    if model is PreservativeGuide:
                        values["normalized_test_name"] = key
                    elif model is TaxonomyTerm:
                        values["normalized_name"] = key
                    elif model is SiteRoute:
                        values["normalized_label"] = normalize_search_text(str(payload["label"]))
                    item = model(**values)
                    session.add(item)
                    session.flush()
                    existing[key] = item
                    inserted += 1
                    if "content_hash" not in columns:
                        session.add(
                            EntityRevision(
                                sync_run_id=run.id,
                                entity_type=dataset,
                                entity_id=item.id,
                                content_hash=digest,
                                snapshot=payload,
                                changed_fields=payload,
                            )
                        )
                    continue
                old_payload = {name: getattr(item, name) for name in payload if name in columns}
                old_digest = getattr(item, "content_hash", stable_hash(old_payload))
                if old_digest == digest:
                    if "last_seen_at" in columns:
                        item.last_seen_at = now
                    if "status" in columns:
                        item.status = "active"
                    unchanged += 1
                    continue
                for name, value in payload.items():
                    if name in columns:
                        setattr(item, name, value)
                if "content_hash" in columns:
                    item.content_hash = digest
                if "last_seen_at" in columns:
                    item.last_seen_at = now
                if model is SiteRoute:
                    item.normalized_label = normalize_search_text(str(payload["label"]))
                updated += 1
                session.flush()
                session.add(
                    EntityRevision(
                        sync_run_id=run.id,
                        entity_type=dataset,
                        entity_id=item.id,
                        content_hash=digest,
                        snapshot=payload,
                        changed_fields=changed_fields(old_payload, payload),
                    )
                )
            if "status" in columns:
                for key, item in existing.items():
                    if key not in seen and item.status != "inactive":
                        item.status = "inactive"
                        deactivated += 1
            self._complete_run(run, len(pages), len(payloads), inserted, updated, unchanged, deactivated)
        return SyncResult(
            dataset, run_id, len(pages), len(payloads), inserted, updated, unchanged, deactivated
        )

    @staticmethod
    def _store_pages(session, source_id: int, page_type: str, pages, now: datetime) -> None:  # type: ignore[no-untyped-def]
        existing = {
            item.url: item
            for item in session.scalars(select(SourcePage).where(SourcePage.data_source_id == source_id))
        }
        for page_number, url, html in pages:
            page = existing.get(url)
            if page is None:
                page = SourcePage(
                    data_source_id=source_id,
                    page_type=page_type,
                    url=url,
                    page_number=page_number,
                )
                session.add(page)
            page.last_fetched_at = now
            page.last_success_at = now
            page.http_status = 200
            page.content_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()

    @staticmethod
    def _complete_run(run, pages, records, inserted, updated, unchanged, deactivated) -> None:  # type: ignore[no-untyped-def]
        run.status = "completed"
        run.finished_at = utcnow()
        run.pages_requested = pages
        run.pages_succeeded = pages
        run.records_found = records
        run.records_inserted = inserted
        run.records_updated = updated
        run.records_unchanged = unchanged
        run.records_deactivated = deactivated
