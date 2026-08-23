from datetime import UTC, datetime
from types import SimpleNamespace

from app.config import Settings
from app.database import SessionLocal, init_database
from app.models import DataSource, FAQCandidate, PublicDocument, VectorIndexItem
from app.normalization import normalize_search_text
from app.vector_index import OpenAIVectorIndexService, RDBVectorDocumentSource, VectorDocument


class FakeFiles:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []

    def create(self, *, file: object, purpose: str) -> SimpleNamespace:
        file_id = f"file_{len(self.created) + 1}"
        self.created.append(file_id)
        assert purpose == "assistants"
        assert file.name.endswith(".md")
        return SimpleNamespace(id=file_id)

    def delete(self, file_id: str) -> None:
        self.deleted.append(file_id)


class FakeVectorStoreFiles:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def retrieve(self, file_id: str, *, vector_store_id: str) -> SimpleNamespace:
        assert vector_store_id == "vs_test"
        return SimpleNamespace(id=file_id, status="completed", usage_bytes=123)

    def delete(self, file_id: str, *, vector_store_id: str) -> None:
        assert vector_store_id == "vs_test"
        self.deleted.append(file_id)


class FakeFileBatches:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, object]]] = []

    def create_and_poll(self, vector_store_id: str, *, files: list[dict[str, object]]) -> None:
        assert vector_store_id == "vs_test"
        self.calls.append(files)


class FakeClient:
    def __init__(self) -> None:
        self.files = FakeFiles()
        self.vector_stores = SimpleNamespace(
            files=FakeVectorStoreFiles(),
            file_batches=FakeFileBatches(),
        )


class StaticSource:
    def __init__(self, documents: list[VectorDocument]) -> None:
        self.documents = documents

    def list_documents(self, types: set[str] | None, limit: int | None) -> list[VectorDocument]:
        return self.documents[:limit] if limit else self.documents


def _settings() -> Settings:
    return Settings(openai_api_key="test-key", openai_vector_store_id="vs_test")


def _document(content_hash: str = "a" * 64) -> VectorDocument:
    return VectorDocument(
        local_ref="document:1",
        entity_type="document",
        entity_id="1",
        file_name="document-1.md",
        content="# 문서\n\n본문",
        content_hash=content_hash,
        source_updated_at=datetime.now(UTC),
        attributes={"entity_type": "document", "local_ref": "document:1"},
    )


def test_rdb_source_exports_only_active_public_body_and_published_faq() -> None:
    init_database()
    with SessionLocal.begin() as session:
        source = DataSource(key="VECTOR_TEST", name="vector test", base_url="https://www.scllab.co.kr")
        session.add(source)
        session.flush()
        published_faq = FAQCandidate(
            fingerprint="c" * 64,
            canonical_question="결과는 어디서 보나요?",
            normalized_question=normalize_search_text("결과는 어디서 보나요?"),
            canonical_answer="결과조회 메뉴에서 확인합니다.",
            source_refs=[],
            status="published",
        )
        draft_faq = FAQCandidate(
            fingerprint="d" * 64,
            canonical_question="초안 질문",
            normalized_question="초안 질문",
            canonical_answer="초안 답변",
            source_refs=[],
            status="draft",
        )
        session.add_all(
            [
                PublicDocument(
                    data_source_id=source.id,
                    source_external_key="vector-doc",
                    document_type="notice",
                    title="연휴 검사 일정",
                    normalized_title=normalize_search_text("연휴 검사 일정"),
                    body_text="연휴에는 검사 시간이 변경됩니다.",
                    source_url="https://www.scllab.co.kr/notice",
                    content_hash="b" * 64,
                ),
                published_faq,
                draft_faq,
            ]
        )
        session.flush()
        published_ref = f"faq:{published_faq.id}"
        draft_ref = f"faq:{draft_faq.id}"

    documents = RDBVectorDocumentSource().list_documents({"document", "faq"})

    refs = {item.local_ref for item in documents}
    assert any(ref.startswith("document:") for ref in refs)
    assert published_ref in refs
    assert draft_ref not in refs
    assert all("초안 답변" not in item.content for item in documents)


def test_sync_uploads_batch_and_persists_completed_mapping() -> None:
    client = FakeClient()
    service = OpenAIVectorIndexService(
        _settings(),
        client=client,
        source=StaticSource([_document()]),
    )

    report = service.sync()

    assert report.created == 1
    assert report.failed == 0
    assert len(client.vector_stores.file_batches.calls) == 1
    with SessionLocal() as session:
        item = session.query(VectorIndexItem).filter_by(
            vector_store_id="vs_test", local_ref="document:1"
        ).one()
        assert item.index_status == "completed"
        assert item.openai_file_id == "file_1"
        assert item.usage_bytes == 123


def test_unchanged_sync_is_idempotent_and_dry_run_has_no_external_writes() -> None:
    client = FakeClient()
    service = OpenAIVectorIndexService(
        _settings(),
        client=client,
        source=StaticSource([_document()]),
    )
    service.sync()
    first_uploads = len(client.files.created)

    unchanged = service.sync()
    dry_run = service.sync(force=True, dry_run=True)

    assert unchanged.unchanged == 1
    assert len(client.files.created) == first_uploads
    assert dry_run.updated == 1
    assert len(client.files.created) == first_uploads


def test_delete_stale_removes_remote_file_and_marks_mapping_deleted() -> None:
    client = FakeClient()
    initial = OpenAIVectorIndexService(
        _settings(), client=client, source=StaticSource([_document()])
    )
    initial.sync()
    empty = OpenAIVectorIndexService(
        _settings(), client=client, source=StaticSource([])
    )

    report = empty.sync(delete_stale=True)

    assert report.deleted == 1
    assert client.vector_stores.files.deleted == ["file_1"]
    with SessionLocal() as session:
        item = session.query(VectorIndexItem).filter_by(local_ref="document:1").one()
        assert item.index_status == "deleted"


def test_failed_update_keeps_last_completed_mapping() -> None:
    client = FakeClient()
    initial = OpenAIVectorIndexService(
        _settings(), client=client, source=StaticSource([_document("a" * 64)])
    )
    initial.sync()

    def fail_batch(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("batch unavailable")

    client.vector_stores.file_batches.create_and_poll = fail_batch
    updated = OpenAIVectorIndexService(
        _settings(), client=client, source=StaticSource([_document("b" * 64)])
    )

    report = updated.sync()

    assert report.failed == 1
    assert "file_2" in client.files.deleted
    with SessionLocal() as session:
        item = session.query(VectorIndexItem).filter_by(local_ref="document:1").one()
        assert item.index_status == "completed"
        assert item.openai_file_id == "file_1"
        assert item.content_hash == "a" * 64
        assert item.last_error.startswith("update failed; serving previous index")


def test_old_file_cleanup_failure_keeps_new_completed_mapping() -> None:
    client = FakeClient()
    successful_delete = client.vector_stores.files.delete
    initial = OpenAIVectorIndexService(
        _settings(), client=client, source=StaticSource([_document("c" * 64)])
    )
    initial.sync()

    def fail_delete(_file_id: str, *, vector_store_id: str) -> None:
        assert vector_store_id == "vs_test"
        raise RuntimeError("cleanup unavailable")

    client.vector_stores.files.delete = fail_delete
    updated = OpenAIVectorIndexService(
        _settings(), client=client, source=StaticSource([_document("b" * 64)])
    )

    report = updated.sync()

    assert report.failed == 1
    with SessionLocal() as session:
        item = session.query(VectorIndexItem).filter_by(local_ref="document:1").one()
        assert item.index_status == "completed"
        assert item.openai_file_id == "file_2"
        assert item.content_hash == "b" * 64
        assert item.last_error.startswith("old file cleanup failed")
        assert item.metadata_json["cleanup_file_id"] == "file_1"

    client.vector_stores.files.delete = successful_delete
    retry = updated.sync()

    assert retry.failed == 0
    with SessionLocal() as session:
        item = session.query(VectorIndexItem).filter_by(local_ref="document:1").one()
        assert item.index_status == "completed"
        assert item.last_error is None
        assert "cleanup_file_id" not in item.metadata_json
