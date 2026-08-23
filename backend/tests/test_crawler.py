from app.config import Settings
from app.database import Base
from app.models import (
    SourceRecord,
)
from app.models import (
    Test as CatalogTestModel,
)
from app.models import (
    TestRevision as RevisionModel,
)
from app.models import (
    TestVariant as VariantModel,
)
from app.scl_crawler import SCLTestCrawler, ScrapedPage, ScrapedTestRow, parse_list_page
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def sample_html() -> str:
    return """
    <p class="listCnt"><strong>1</strong> / 1 [ 총 <strong>2</strong> 건 ]</p>
    <table class="listTable">
      <tr><th>No.</th><th>검사코드</th><th>검사명</th><th>검사방법</th><th>검체명</th><th>수가</th><th>검사일</th><th>소요일</th></tr>
      <tr onclick="javascript:fnActExamView('45140','A');">
        <td>1</td><td>45140</td><td>신속 CRE 유전자검사</td><td>Real-time PCR</td><td>Rectal swab</td><td>D685102KZ</td><td>월~토<br>/주간</td><td>1일</td>
      </tr>
      <tr onclick="javascript:fnActExamView('45140','B');">
        <td>2</td><td>45140</td><td>신속 CRE 유전자검사</td><td>Real-time PCR</td><td>Other</td><td>D685102KZ</td><td>월~토<br>/야간</td><td>2일</td>
      </tr>
    </table>
    """


def isolated_crawler(monkeypatch) -> tuple[SCLTestCrawler, sessionmaker]:  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("app.scl_crawler.SessionLocal", factory)
    crawler = SCLTestCrawler(
        Settings(
            openai_api_key=None,
            database_url="sqlite:///:memory:",
            seed_demo_on_empty=False,
            scl_inactive_after_misses=2,
        )
    )
    return crawler, factory


def test_parses_duplicate_code_as_distinct_source_rows() -> None:
    page = parse_list_page(sample_html(), page_number=1)

    assert page.total_records == 2
    assert page.rows[0].external_key == "45140:A"
    assert page.rows[1].external_key == "45140:B"
    assert page.rows[0].schedule == "월~토 /주간"


def test_ingestion_is_idempotent_and_preserves_variants(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    crawler, factory = isolated_crawler(monkeypatch)
    page = parse_list_page(sample_html(), page_number=1)

    first_run = crawler._start_run()
    first = crawler._ingest(first_run, [page], complete=True)
    second_run = crawler._start_run()
    second = crawler._ingest(second_run, [page], complete=True)

    assert first.inserted == 2
    assert second.inserted == 0
    assert second.unchanged == 2
    with factory() as session:
        assert session.scalar(select(func.count(CatalogTestModel.id))) == 1
        assert session.scalar(select(func.count(VariantModel.id))) == 2
        assert session.scalar(select(func.count(SourceRecord.id))) == 2
        assert session.scalar(select(func.count(RevisionModel.id))) == 2


def test_changed_value_creates_revision_and_partial_run_does_not_deactivate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    crawler, factory = isolated_crawler(monkeypatch)
    page = parse_list_page(sample_html(), page_number=1)
    crawler._ingest(crawler._start_run(), [page], complete=True)

    changed_row = ScrapedTestRow(
        **{
            **page.rows[0].__dict__,
            "turnaround_time": "3일",
        }
    )
    changed_page = ScrapedPage(
        page_number=1,
        url=page.url,
        http_status=200,
        content_hash=page.content_hash,
        rows=[changed_row],
        total_pages=1,
        total_records=1,
    )
    result = crawler._ingest(crawler._start_run(), [changed_page], complete=False)

    assert result.updated == 1
    with factory() as session:
        changed = session.scalar(select(VariantModel).where(VariantModel.source_row_key == "45140:A"))
        missing = session.scalar(select(VariantModel).where(VariantModel.source_row_key == "45140:B"))
        assert changed is not None and changed.tat_text == "3일"
        assert missing is not None and missing.status == "active"
        assert missing.missing_runs == 0
        assert session.scalar(select(func.count(RevisionModel.id))) == 3
