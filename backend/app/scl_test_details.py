"""Synchronize publicly visible SCL test detail pages."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select

from .config import Settings
from .database import SessionLocal, init_database
from .models import (
    DatasetSyncRun,
    DataSource,
    SourcePage,
    Test,
    TestPublicDetail,
    TestVariant,
    utcnow,
)
from .normalization import clean_text, normalize_search_text

PUBLIC_FIELD_LABELS = {
    "검사명": "검사명",
    "scl검사코드": "SCL 검사코드",
    "검사방법": "검사방법",
    "검체명": "검체명",
    "보존방법": "보존방법",
    "소요량": "소요량",
    "검사요일": "검사요일",
    "검사소요일": "검사소요일",
    "분류번호": "분류번호",
    "급여코드": "급여코드",
    "비급여코드": "비급여코드",
    "검사수가": "검사수가",
    "참고치": "참고치",
    "채취방법및주의사항": "채취방법 및 주의사항",
    "임상적의의": "임상적 의의",
    "증가": "증가",
    "감소": "감소",
    "급여기준": "급여기준",
}


@dataclass(frozen=True)
class ParsedTestDetail:
    source_url: str
    fields: dict[str, str]
    container: dict[str, str]
    full_text: str
    normalized_text: str
    content_hash: str


@dataclass(frozen=True)
class DetailFetchResult:
    variant_id: int
    source_url: str
    detail: ParsedTestDetail | None
    error: str | None = None


@dataclass(frozen=True)
class DetailSyncSummary:
    run_id: int
    status: str
    requested: int
    succeeded: int
    failed: int
    inserted: int
    updated: int
    unchanged: int


def _label_key(value: str) -> str:
    return recompact(value).casefold().replace("/", "").replace(":", "")


def recompact(value: str) -> str:
    return "".join(clean_text(value).split())


def parse_test_detail_page(html: str, source_url: str) -> ParsedTestDetail:
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, str] = {}
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        canonical = PUBLIC_FIELD_LABELS.get(_label_key(cells[0].get_text(" ", strip=True)))
        if not canonical:
            continue
        value = clean_text(cells[1].get_text(" ", strip=True))
        if value and value != "-":
            fields[canonical] = value

    container: dict[str, str] = {}
    for key_node in soup.select(".key"):
        value_node = key_node.find_next_sibling(class_="value")
        key = clean_text(key_node.get_text(" ", strip=True)).replace("/ 참고", "/참고")
        value = clean_text(value_node.get_text(" ", strip=True)) if value_node else ""
        if key and value:
            container[key] = value

    if not fields.get("검사명") or not fields.get("SCL 검사코드"):
        raise ValueError("Public test detail fields were not found")

    lines = [f"{key}: {value}" for key, value in fields.items()]
    lines.extend(f"용기 {key}: {value}" for key, value in container.items())
    full_text = "\n".join(lines)
    canonical_payload = json.dumps(
        {"fields": fields, "container": container}, ensure_ascii=False, sort_keys=True
    )
    return ParsedTestDetail(
        source_url=source_url,
        fields=fields,
        container=container,
        full_text=full_text,
        normalized_text=normalize_search_text(full_text),
        content_hash=hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
    )


class SCLTestDetailSync:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def sync(
        self,
        *,
        limit: int | None = None,
        workers: int = 4,
        only_missing: bool = False,
        progress: Callable[[int, int, int], None] | None = None,
    ) -> DetailSyncSummary:
        init_database()
        with SessionLocal.begin() as session:
            source = session.scalar(select(DataSource).where(DataSource.key == "SCL_PUBLIC"))
            if source is None:
                raise RuntimeError("SCL public catalog must be synchronized first")
            statement = (
                select(TestVariant.id, TestVariant.detail_url)
                .join(Test)
                .where(Test.data_source_id == source.id, TestVariant.status == "active")
                .order_by(TestVariant.id)
            )
            if only_missing:
                statement = statement.outerjoin(TestPublicDetail).where(TestPublicDetail.id.is_(None))
            if limit:
                statement = statement.limit(limit)
            targets = [(int(row.id), str(row.detail_url)) for row in session.execute(statement)]
            run = DatasetSyncRun(data_source_id=source.id, dataset="test_details", status="running")
            session.add(run)
            session.flush()
            run_id = run.id

        results: list[DetailFetchResult] = []
        failed = 0
        client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "SCLChatPrototype/0.1 (public test detail sync)"},
            limits=httpx.Limits(max_connections=max(1, workers), max_keepalive_connections=max(1, workers)),
        )
        try:
            with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
                futures = {
                    executor.submit(self._fetch_one, client, variant_id, url): variant_id
                    for variant_id, url in targets
                }
                for completed, future in enumerate(as_completed(futures), start=1):
                    result = future.result()
                    results.append(result)
                    if result.error:
                        failed += 1
                    if progress and (completed == len(targets) or completed % 100 == 0):
                        progress(completed, len(targets), failed)
        finally:
            client.close()

        inserted = updated = unchanged = 0
        now = datetime.now(UTC)
        successful = [result for result in results if result.detail is not None]
        with SessionLocal.begin() as session:
            existing = {
                item.test_variant_id: item
                for item in session.scalars(
                    select(TestPublicDetail).where(
                        TestPublicDetail.test_variant_id.in_([item.variant_id for item in successful])
                    )
                )
            }
            pages = {
                item.url: item
                for item in session.scalars(
                    select(SourcePage).where(SourcePage.url.in_([item.source_url for item in successful]))
                )
            }
            source_id = session.scalar(select(DataSource.id).where(DataSource.key == "SCL_PUBLIC"))
            if source_id is None:
                raise RuntimeError("SCL public data source disappeared")
            for result in successful:
                detail = result.detail
                assert detail is not None
                page = pages.get(detail.source_url)
                if page is None:
                    page = SourcePage(
                        data_source_id=source_id,
                        page_type="test_detail",
                        url=detail.source_url,
                        title=detail.fields.get("검사명"),
                    )
                    session.add(page)
                    session.flush()
                    pages[detail.source_url] = page
                page.last_fetched_at = now
                page.last_success_at = now
                page.content_hash = detail.content_hash
                page.http_status = 200

                model = existing.get(result.variant_id)
                if model is None:
                    model = TestPublicDetail(
                        test_variant_id=result.variant_id,
                        source_page_id=page.id,
                        source_url=detail.source_url,
                        fields_json=detail.fields,
                        container_json=detail.container,
                        full_text=detail.full_text,
                        normalized_text=detail.normalized_text,
                        content_hash=detail.content_hash,
                        last_fetched_at=now,
                        last_success_at=now,
                    )
                    session.add(model)
                    inserted += 1
                elif model.content_hash == detail.content_hash:
                    model.last_fetched_at = now
                    model.last_success_at = now
                    model.fetch_status = "completed"
                    model.last_error = None
                    unchanged += 1
                else:
                    model.source_page_id = page.id
                    model.source_url = detail.source_url
                    model.fields_json = detail.fields
                    model.container_json = detail.container
                    model.full_text = detail.full_text
                    model.normalized_text = detail.normalized_text
                    model.content_hash = detail.content_hash
                    model.fetch_status = "completed"
                    model.last_error = None
                    model.last_fetched_at = now
                    model.last_success_at = now
                    updated += 1

            run = session.get(DatasetSyncRun, run_id)
            if run is None:
                raise RuntimeError("Test detail sync run disappeared")
            run.status = "completed" if failed == 0 else "partial_completed"
            run.finished_at = utcnow()
            run.pages_requested = len(targets)
            run.pages_succeeded = len(successful)
            run.records_found = len(successful)
            run.records_inserted = inserted
            run.records_updated = updated
            run.records_unchanged = unchanged
            run.error_summary = (
                "\n".join(f"{item.source_url}: {item.error}" for item in results if item.error)[:4000] or None
            )

        return DetailSyncSummary(
            run_id=run_id,
            status="completed" if failed == 0 else "partial_completed",
            requested=len(targets),
            succeeded=len(successful),
            failed=failed,
            inserted=inserted,
            updated=updated,
            unchanged=unchanged,
        )

    def _fetch_one(self, client: httpx.Client, variant_id: int, url: str) -> DetailFetchResult:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                if self.settings.scl_crawl_delay_seconds:
                    time.sleep(self.settings.scl_crawl_delay_seconds)
                response = client.get(url)
                response.raise_for_status()
                return DetailFetchResult(
                    variant_id=variant_id,
                    source_url=url,
                    detail=parse_test_detail_page(response.text, url),
                )
            except (httpx.HTTPError, ValueError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(attempt + 1)
        return DetailFetchResult(variant_id, url, None, str(last_error)[:500])
