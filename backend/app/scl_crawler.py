from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .database import SessionLocal, init_database
from .models import (
    BillingCode,
    DataSource,
    IngestionRun,
    Method,
    SourcePage,
    SourceRecord,
    Specimen,
    Test,
    TestAlias,
    TestBillingCode,
    TestRevision,
    TestVariant,
    utcnow,
)
from .normalization import (
    changed_fields,
    clean_text,
    normalize_search_text,
    parse_schedule,
    parse_turnaround_days,
    specimen_group,
)

SCL_SOURCE_KEY = "SCL_PUBLIC"
SCL_BASE_URL = "https://www.scllab.co.kr"
SCL_LIST_PATH = "/front/check/check_item_list.do"
SCL_DETAIL_PATH = "/front/check/check_item_detail.do"


@dataclass(frozen=True)
class ScrapedTestRow:
    source_order: int
    test_code: str
    sample_code: str
    name: str
    method: str
    specimen: str
    billing_code: str
    schedule: str
    turnaround_time: str
    page_number: int
    source_url: str
    detail_url: str

    @property
    def external_key(self) -> str:
        return f"{self.test_code}:{self.sample_code}"

    def payload(self) -> dict[str, object]:
        # Page number and list position change when SCL inserts a new row; they are
        # provenance, not a material test-data change.
        return {
            "test_code": self.test_code,
            "sample_code": self.sample_code,
            "name": self.name,
            "method": self.method,
            "specimen": self.specimen,
            "billing_code": self.billing_code,
            "schedule": self.schedule,
            "turnaround_time": self.turnaround_time,
            "detail_url": self.detail_url,
        }

    def content_hash(self) -> str:
        canonical = json.dumps(self.payload(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ScrapedPage:
    page_number: int
    url: str
    http_status: int
    content_hash: str
    rows: list[ScrapedTestRow]
    total_pages: int
    total_records: int


@dataclass(frozen=True)
class CrawlSummary:
    run_id: int
    status: str
    pages: int
    records: int
    inserted: int
    updated: int
    unchanged: int
    deactivated: int


def _page_url(page_number: int, page_size: int) -> str:
    query = urlencode({"pageIndex": page_number, "pageUnit": page_size})
    return f"{SCL_BASE_URL}{SCL_LIST_PATH}?{query}"


def parse_list_page(html: str, page_number: int, page_size: int = 50) -> ScrapedPage:
    soup = BeautifulSoup(html, "html.parser")
    count_node = soup.select_one(".listCnt")
    if count_node is None:
        raise ValueError("SCL list count was not found")

    count_text = clean_text(count_node.get_text(" ", strip=True))
    count_match = re.search(r"(\d+)\s*/\s*(\d+)\s*\[\s*총\s*(\d+)\s*건", count_text)
    if count_match is None:
        raise ValueError(f"Unexpected SCL list count format: {count_text}")
    total_pages = int(count_match.group(2))
    total_records = int(count_match.group(3))
    url = _page_url(page_number, page_size)

    rows: list[ScrapedTestRow] = []
    for row_node in soup.select("table.listTable tr[onclick*='fnActExamView']"):
        onclick = row_node.get("onclick", "")
        key_match = re.search(r"fnActExamView\('([^']+)'\s*,\s*'([^']+)'\)", onclick)
        cells = row_node.find_all("td")
        if key_match is None or len(cells) < 8:
            continue
        values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells[:8]]
        test_code, sample_code = key_match.groups()
        detail_query = urlencode({"itemcode": test_code, "sampcode": sample_code})
        rows.append(
            ScrapedTestRow(
                source_order=int(re.sub(r"\D", "", values[0]) or 0),
                test_code=clean_text(test_code),
                sample_code=clean_text(sample_code),
                name=values[2],
                method=values[3],
                specimen=values[4],
                billing_code=values[5],
                schedule=values[6],
                turnaround_time=values[7],
                page_number=page_number,
                source_url=url,
                detail_url=f"{SCL_BASE_URL}{SCL_DETAIL_PATH}?{detail_query}",
            )
        )

    if not rows:
        raise ValueError(f"No SCL test rows found on page {page_number}")
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
    return ScrapedPage(page_number, url, 200, digest, rows, total_pages, total_records)


class SCLTestCrawler:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client

    def fetch_all(self, max_pages: int | None = None) -> list[ScrapedPage]:
        page_size = min(max(self.settings.scl_crawl_page_size, 10), 50)
        owned_client = self._client is None
        client = self._client or httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "SCLChatPrototype/0.1 (public catalog sync)"},
        )
        try:
            first = self._fetch_page(client, 1, page_size)
            total_pages = min(first.total_pages, max_pages) if max_pages else first.total_pages
            pages = [first]
            for page_number in range(2, total_pages + 1):
                if self.settings.scl_crawl_delay_seconds:
                    time.sleep(self.settings.scl_crawl_delay_seconds)
                pages.append(self._fetch_page(client, page_number, page_size))

            expected_records = first.total_records if total_pages == first.total_pages else None
            actual_records = sum(len(page.rows) for page in pages)
            if expected_records is not None and actual_records != expected_records:
                raise ValueError(
                    f"Incomplete SCL crawl: expected {expected_records} rows, parsed {actual_records}"
                )
            return pages
        finally:
            if owned_client:
                client.close()

    @staticmethod
    def _fetch_page(client: httpx.Client, page_number: int, page_size: int) -> ScrapedPage:
        url = _page_url(page_number, page_size)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = client.get(url)
                response.raise_for_status()
                return parse_list_page(response.text, page_number, page_size)
            except (httpx.HTTPError, ValueError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"Failed to fetch SCL page {page_number}: {last_error}")

    def crawl_and_ingest(self, max_pages: int | None = None) -> CrawlSummary:
        init_database()
        run_id = self._start_run()
        try:
            pages = self.fetch_all(max_pages=max_pages)
            return self._ingest(run_id, pages, complete=max_pages is None)
        except Exception as error:
            with SessionLocal.begin() as session:
                run = session.get(IngestionRun, run_id)
                if run:
                    run.status = "failed"
                    run.finished_at = utcnow()
                    run.error_summary = str(error)[:4000]
            raise

    def _start_run(self) -> int:
        with SessionLocal.begin() as session:
            source = _get_or_create_source(session)
            run = IngestionRun(data_source_id=source.id, status="running")
            session.add(run)
            session.flush()
            return run.id

    def _ingest(self, run_id: int, pages: list[ScrapedPage], complete: bool) -> CrawlSummary:
        now = datetime.now(UTC)
        inserted = updated = unchanged = deactivated = 0
        all_rows = [row for page in pages for row in page.rows]
        seen_row_keys = {row.external_key for row in all_rows}
        seen_codes = {row.test_code for row in all_rows}

        with SessionLocal.begin() as session:
            run = session.get(IngestionRun, run_id)
            if run is None:
                raise RuntimeError(f"Ingestion run {run_id} disappeared")
            source = session.get(DataSource, run.data_source_id)
            if source is None:
                raise RuntimeError("SCL data source disappeared")

            page_map = _upsert_source_pages(session, source.id, pages, now)
            tests = {
                item.source_test_code: item
                for item in session.scalars(select(Test).where(Test.data_source_id == source.id))
            }
            variants = {
                item.source_row_key: item
                for item in session.scalars(
                    select(TestVariant).join(Test).where(Test.data_source_id == source.id)
                )
            }
            methods = {item.normalized_name: item for item in session.scalars(select(Method))}
            specimens = {item.normalized_name: item for item in session.scalars(select(Specimen))}
            billing_codes = {
                (item.code_system, item.code): item for item in session.scalars(select(BillingCode))
            }
            current_records = {
                item.external_key: item
                for item in session.scalars(
                    select(SourceRecord).where(
                        SourceRecord.data_source_id == source.id,
                        SourceRecord.is_current.is_(True),
                    )
                )
            }

            for row in all_rows:
                test = tests.get(row.test_code)
                if test is None:
                    test = Test(
                        data_source_id=source.id,
                        source_test_code=row.test_code,
                        name=row.name,
                        normalized_name=normalize_search_text(row.name),
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    session.add(test)
                    session.flush()
                    tests[row.test_code] = test
                else:
                    test.status = "active"
                    test.missing_runs = 0
                    test.last_seen_at = now

                alias_value = normalize_search_text(row.name)
                if alias_value and alias_value != test.normalized_name:
                    existing_alias = session.scalar(
                        select(TestAlias).where(
                            TestAlias.test_id == test.id,
                            TestAlias.normalized_alias == alias_value,
                        )
                    )
                    if existing_alias is None:
                        session.add(
                            TestAlias(
                                test_id=test.id,
                                alias=row.name,
                                normalized_alias=alias_value,
                            )
                        )

                method = _get_or_create_method(session, methods, row.method)
                specimen = _get_or_create_specimen(session, specimens, row.specimen)
                tat_min, tat_max = parse_turnaround_days(row.turnaround_time)
                schedule_days, shift = parse_schedule(row.schedule)

                variant = variants.get(row.external_key)
                row_payload = row.payload()
                row_hash = row.content_hash()
                if variant is None:
                    variant = TestVariant(
                        test_id=test.id,
                        source_sample_code=row.sample_code,
                        source_row_key=row.external_key,
                        display_name=row.name,
                        method_id=method.id if method else None,
                        specimen_id=specimen.id if specimen else None,
                        container_text=None,
                        billing_code_text=row.billing_code,
                        schedule_text=row.schedule,
                        schedule_days=schedule_days,
                        schedule_shift=shift,
                        tat_text=row.turnaround_time,
                        tat_min_days=tat_min,
                        tat_max_days=tat_max,
                        detail_url=row.detail_url,
                        source_page_id=page_map[row.page_number].id,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    session.add(variant)
                    session.flush()
                    variants[row.external_key] = variant
                    inserted += 1
                else:
                    variant.display_name = row.name
                    variant.method_id = method.id if method else None
                    variant.specimen_id = specimen.id if specimen else None
                    variant.container_text = None
                    variant.billing_code_text = row.billing_code
                    variant.schedule_text = row.schedule
                    variant.schedule_days = schedule_days
                    variant.schedule_shift = shift
                    variant.tat_text = row.turnaround_time
                    variant.tat_min_days = tat_min
                    variant.tat_max_days = tat_max
                    variant.detail_url = row.detail_url
                    variant.source_page_id = page_map[row.page_number].id
                    variant.status = "active"
                    variant.missing_runs = 0
                    variant.last_seen_at = now

                _sync_billing_codes(session, variant, billing_codes, row.billing_code)

                current_record = current_records.get(row.external_key)
                if current_record and current_record.content_hash == row_hash:
                    current_record.last_seen_at = now
                    current_record.source_page_id = page_map[row.page_number].id
                    current_record.ingestion_run_id = run.id
                    current_record.test_id = test.id
                    current_record.test_variant_id = variant.id
                    unchanged += 1
                    continue

                before_payload = current_record.raw_payload if current_record else {}
                if current_record:
                    current_record.is_current = False
                source_record = SourceRecord(
                    ingestion_run_id=run.id,
                    data_source_id=source.id,
                    source_page_id=page_map[row.page_number].id,
                    external_key=row.external_key,
                    content_hash=row_hash,
                    raw_payload=row_payload,
                    test_id=test.id,
                    test_variant_id=variant.id,
                    first_seen_at=now,
                    last_seen_at=now,
                    is_current=True,
                )
                session.add(source_record)
                session.flush()
                session.add(
                    TestRevision(
                        test_id=test.id,
                        test_variant_id=variant.id,
                        source_record_id=source_record.id,
                        content_hash=row_hash,
                        snapshot=row_payload,
                        changed_fields=changed_fields(before_payload, row_payload),
                    )
                )
                if current_record:
                    updated += 1

            if complete:
                for key, variant in variants.items():
                    if key not in seen_row_keys:
                        variant.missing_runs += 1
                        if variant.missing_runs >= self.settings.scl_inactive_after_misses:
                            if variant.status != "inactive":
                                deactivated += 1
                            variant.status = "inactive"
                for code, test in tests.items():
                    if code not in seen_codes:
                        test.missing_runs += 1
                        if test.missing_runs >= self.settings.scl_inactive_after_misses:
                            test.status = "inactive"

            run.status = "completed" if complete else "partial_completed"
            run.finished_at = now
            run.pages_expected = pages[0].total_pages
            run.pages_requested = len(pages)
            run.pages_succeeded = len(pages)
            run.records_found = len(all_rows)
            run.records_inserted = inserted
            run.records_updated = updated
            run.records_unchanged = unchanged
            run.records_deactivated = deactivated

        return CrawlSummary(
            run_id=run_id,
            status="completed" if complete else "partial_completed",
            pages=len(pages),
            records=len(all_rows),
            inserted=inserted,
            updated=updated,
            unchanged=unchanged,
            deactivated=deactivated,
        )


def _get_or_create_source(session: Session) -> DataSource:
    source = session.scalar(select(DataSource).where(DataSource.key == SCL_SOURCE_KEY))
    if source is None:
        source = DataSource(
            key=SCL_SOURCE_KEY,
            name="SCL 공개 검사항목조회",
            base_url=SCL_BASE_URL,
            source_type="public_web",
        )
        session.add(source)
        session.flush()
    return source


def _upsert_source_pages(
    session: Session, source_id: int, pages: list[ScrapedPage], now: datetime
) -> dict[int, SourcePage]:
    existing = {
        item.url: item
        for item in session.scalars(select(SourcePage).where(SourcePage.data_source_id == source_id))
    }
    result: dict[int, SourcePage] = {}
    for page in pages:
        model = existing.get(page.url)
        if model is None:
            model = SourcePage(
                data_source_id=source_id,
                page_type="test_list",
                url=page.url,
                title="SCL 검사항목조회",
                page_number=page.page_number,
            )
            session.add(model)
            session.flush()
        model.last_fetched_at = now
        model.last_success_at = now
        model.content_hash = page.content_hash
        model.http_status = page.http_status
        result[page.page_number] = model
    return result


def _get_or_create_method(session: Session, cache: dict[str, Method], value: str) -> Method | None:
    normalized = normalize_search_text(value)
    if not normalized or value == "-":
        return None
    model = cache.get(normalized)
    if model is None:
        model = Method(name=value, normalized_name=normalized)
        session.add(model)
        session.flush()
        cache[normalized] = model
    return model


def _get_or_create_specimen(session: Session, cache: dict[str, Specimen], value: str) -> Specimen | None:
    normalized = normalize_search_text(value)
    if not normalized or value == "-":
        return None
    model = cache.get(normalized)
    if model is None:
        model = Specimen(
            canonical_name=value,
            normalized_name=normalized,
            specimen_group=specimen_group(value),
        )
        session.add(model)
        session.flush()
        cache[normalized] = model
    return model


def _sync_billing_codes(
    session: Session,
    variant: TestVariant,
    cache: dict[tuple[str, str], BillingCode],
    value: str,
) -> None:
    clean_value = clean_text(value)
    if not clean_value or clean_value == "-":
        return
    key = ("SCL_PUBLIC", clean_value)
    billing = cache.get(key)
    if billing is None:
        billing = BillingCode(code_system=key[0], code=key[1])
        session.add(billing)
        session.flush()
        cache[key] = billing
    exists = session.scalar(
        select(TestBillingCode.id).where(
            TestBillingCode.test_variant_id == variant.id,
            TestBillingCode.billing_code_id == billing.id,
        )
    )
    if exists is None:
        session.add(
            TestBillingCode(
                test_variant_id=variant.id,
                billing_code_id=billing.id,
                is_primary=True,
            )
        )
