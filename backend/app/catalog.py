from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from .config import Settings, settings
from .database import SessionLocal, init_database
from .models import (
    DataSource,
    IngestionRun,
    Method,
    Specimen,
    Test,
    TestAlias,
    TestBillingCode,
    TestPublicDetail,
    TestVariant,
    utcnow,
)
from .normalization import normalize_search_text, parse_schedule, parse_turnaround_days
from .schemas import TestInfo

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "data" / "fixtures" / "tests.json"
SEARCH_STOP_WORDS = {
    "검사",
    "검체",
    "용기",
    "결과",
    "관련",
    "알려줘",
    "뭐야",
    "찾아줘",
    "소요일",
    "정보",
    "주의사항",
    "주의",
    "방법",
    "가능",
    "가능한가요",
    "전",
    "전에",
    "어떤",
    "있나요",
}
SPECIMEN_GROUP_TERMS = {
    "urine": "urine 소변 요",
    "serum": "serum 혈청",
    "plasma": "plasma 혈장",
    "whole_blood": "whole blood 전혈 혈액",
    "stool": "stool 분변 대변",
    "tissue": "tissue 조직",
    "swab": "swab 도말",
    "csf": "csf 뇌척수액",
    "body_fluid": "body fluid 체액",
}


@dataclass(frozen=True)
class CatalogSearchRow:
    info: TestInfo
    name: str
    aliases: tuple[str, ...]
    specimen: str
    specimen_terms: str
    method: str
    billing: str
    schedule: str
    tat: str
    code: str
    detail_primary: str
    detail_precautions: str
    haystack: str


class DatabaseCatalog:
    def __init__(self, app_settings: Settings = settings, session_factory=None) -> None:
        self.session_factory = session_factory or SessionLocal
        self.settings = app_settings
        self._search_cache_lock = threading.Lock()
        self._search_cache_signature: tuple[object, ...] | None = None
        self._search_cache: tuple[CatalogSearchRow, ...] = ()
        init_database(self.session_factory.kw["bind"])
        if self.settings.seed_demo_on_empty:
            self._seed_demo_if_empty()

    def get(self, code: str | None, variant_key: str | None = None) -> TestInfo | None:
        if not code and not variant_key:
            return None
        with self.session_factory() as session:
            statement = self._base_statement()
            if variant_key:
                statement = statement.where(TestVariant.source_row_key == variant_key)
                if code:
                    statement = statement.where(func.lower(Test.source_test_code) == code.casefold())
            else:
                statement = statement.where(func.lower(Test.source_test_code) == code.casefold())
            variants = list(session.scalars(statement.order_by(TestVariant.id).limit(2)).unique())
            if not variants:
                return None
            if not variant_key and len(variants) > 1:
                return None
            return self._to_info(variants[0])

    def search(self, query: str, limit: int = 10) -> list[TestInfo]:
        normalized_query = normalize_search_text(query)
        terms = [term for term in normalized_query.split() if len(term) > 1 and term not in SEARCH_STOP_WORDS]
        if not normalized_query:
            return []

        rows = self.search_rows()

        ranked: list[tuple[int, str, TestInfo]] = []
        for row in rows:
            aliases = row.aliases
            name = row.name
            specimen = row.specimen
            specimen_terms = row.specimen_terms
            method = row.method
            billing = row.billing
            schedule = row.schedule
            tat = row.tat
            code = row.code
            detail_primary = row.detail_primary
            detail_precautions = row.detail_precautions
            haystack = row.haystack

            score = 0
            if normalized_query == code:
                score += 200
            elif code in normalized_query:
                score += 80
            if normalized_query == name or normalized_query in aliases:
                score += 150
            matched_terms = sum(1 for term in terms if term in haystack)
            score += matched_terms * 30
            if terms and matched_terms == len(terms):
                score += 100
            for term in terms:
                if term in name:
                    score += 15
                if any(term in alias for alias in aliases):
                    score += 12
                if term in specimen:
                    score += 7
                if term in specimen_terms:
                    score += 7
                if term in method:
                    score += 5
                if term in billing:
                    score += 4
                if term in tat:
                    score += 6
                if term in schedule:
                    score += 3
                if term in detail_primary:
                    score += 18
                if "주의" in normalized_query and term in detail_precautions:
                    score += 24
                if term in haystack and score == 0:
                    score += 1
            if score:
                ranked.append((score, name, row.info))

        ranked.sort(key=lambda item: (-item[0], item[1], item[2].variant_key or item[2].code))
        return [info for _, _, info in ranked[:limit]]

    def search_rows(self) -> tuple[CatalogSearchRow, ...]:
        with self.session_factory() as session:
            source_key = self._preferred_source_key(session)
            count, max_id, latest_seen, latest_updated = session.execute(
                select(
                    func.count(TestVariant.id),
                    func.max(TestVariant.id),
                    func.max(TestVariant.last_seen_at),
                    func.max(TestVariant.updated_at),
                )
                .join(TestVariant.test)
                .join(Test.data_source)
                .where(
                    DataSource.key == source_key,
                    Test.status == "active",
                    TestVariant.status == "active",
                )
            ).one()
            detail_count, detail_latest = session.execute(
                select(func.count(TestPublicDetail.id), func.max(TestPublicDetail.updated_at))
            ).one()
            signature = (
                source_key,
                int(count or 0),
                int(max_id or 0),
                latest_seen,
                latest_updated,
                int(detail_count or 0),
                detail_latest,
            )
            if signature == self._search_cache_signature:
                return self._search_cache

            with self._search_cache_lock:
                if signature == self._search_cache_signature:
                    return self._search_cache
                statement = self._base_statement().where(
                    DataSource.key == source_key,
                    Test.status == "active",
                    TestVariant.status == "active",
                )
                variants = list(session.scalars(statement).unique())
                rows: list[CatalogSearchRow] = []
                for variant in variants:
                    test = variant.test
                    aliases = tuple(normalize_search_text(item.alias) for item in test.aliases)
                    name = normalize_search_text(variant.display_name)
                    specimen = normalize_search_text(
                        variant.specimen.canonical_name if variant.specimen else ""
                    )
                    specimen_terms = normalize_search_text(
                        SPECIMEN_GROUP_TERMS.get(
                            variant.specimen.specimen_group if variant.specimen else "",
                            "",
                        )
                    )
                    method = normalize_search_text(variant.method.name if variant.method else "")
                    # Normalized links are authoritative; raw text remains source provenance.
                    linked_codes = [link.billing_code.code for link in variant.billing_codes]
                    billing = normalize_search_text(
                        " ".join(linked_codes) if linked_codes else variant.billing_code_text
                    )
                    schedule = normalize_search_text(variant.schedule_text)
                    tat = normalize_search_text(variant.tat_text)
                    code = test.source_test_code.casefold()
                    detail_text = normalize_search_text(
                        variant.public_detail.normalized_text if variant.public_detail else ""
                    )
                    detail_fields = variant.public_detail.fields_json if variant.public_detail else {}
                    detail_precautions = normalize_search_text(detail_fields.get("채취방법 및 주의사항", ""))
                    detail_primary = normalize_search_text(
                        " ".join(
                            [
                                detail_precautions,
                                detail_fields.get("임상적 의의", ""),
                            ]
                        )
                    )
                    rows.append(
                        CatalogSearchRow(
                            info=self._to_info(variant),
                            name=name,
                            aliases=aliases,
                            specimen=specimen,
                            specimen_terms=specimen_terms,
                            method=method,
                            billing=billing,
                            schedule=schedule,
                            tat=tat,
                            code=code,
                            detail_primary=detail_primary,
                            detail_precautions=detail_precautions,
                            haystack=" ".join(
                                [
                                    name,
                                    *aliases,
                                    specimen,
                                    specimen_terms,
                                    method,
                                    billing,
                                    schedule,
                                    tat,
                                    code,
                                    detail_text,
                                ]
                            ),
                        )
                    )
                self._search_cache = tuple(rows)
                self._search_cache_signature = signature
                return self._search_cache

    def prompt_snapshot(self, items: list[TestInfo] | None = None) -> str:
        selected = items if items is not None else []
        return json.dumps(
            [
                {
                    "code": item.code,
                    "variant_key": item.variant_key,
                    "name": item.name,
                    "aliases": item.aliases[:4],
                    "specimen": item.specimen,
                    "container": item.container,
                    "method": item.method,
                    "schedule": item.schedule,
                    "tat": item.tat,
                    "public_details": self._prompt_details(item.public_details),
                    "source_url": str(item.source_url) if item.source_url else None,
                    "demo": item.demo,
                }
                for item in selected[:10]
            ],
            ensure_ascii=False,
        )

    def status(self) -> dict[str, object]:
        with self.session_factory() as session:
            source_key = self._preferred_source_key(session)
            test_count = (
                session.scalar(
                    select(func.count(Test.id))
                    .join(DataSource)
                    .where(DataSource.key == source_key, Test.status == "active")
                )
                or 0
            )
            variant_count = (
                session.scalar(
                    select(func.count(TestVariant.id))
                    .join(Test)
                    .join(DataSource)
                    .where(DataSource.key == source_key, TestVariant.status == "active")
                )
                or 0
            )
            detail_count = (
                session.scalar(
                    select(func.count(TestPublicDetail.id))
                    .join(TestVariant)
                    .join(Test)
                    .join(DataSource)
                    .where(DataSource.key == source_key, TestPublicDetail.fetch_status == "completed")
                )
                or 0
            )
            last_run = session.scalar(
                select(IngestionRun)
                .join(DataSource, IngestionRun.data_source_id == DataSource.id)
                .where(DataSource.key == source_key)
                .order_by(IngestionRun.started_at.desc())
                .limit(1)
            )
            return {
                "source": source_key,
                "tests": test_count,
                "variants": variant_count,
                "details": detail_count,
                "last_sync_at": (
                    (
                        last_run.finished_at.replace(tzinfo=UTC)
                        if last_run.finished_at.tzinfo is None
                        else last_run.finished_at.astimezone(UTC)
                    ).isoformat()
                    if last_run and last_run.finished_at
                    else None
                ),
                "last_sync_status": last_run.status if last_run else None,
            }

    @staticmethod
    def _base_statement():
        return (
            select(TestVariant)
            .join(TestVariant.test)
            .join(Test.data_source)
            .options(
                joinedload(TestVariant.test).joinedload(Test.data_source),
                joinedload(TestVariant.method),
                joinedload(TestVariant.specimen),
                joinedload(TestVariant.test).selectinload(Test.aliases),
                joinedload(TestVariant.public_detail),
                selectinload(TestVariant.billing_codes).joinedload(TestBillingCode.billing_code),
            )
        )

    @staticmethod
    def _preferred_source_key(session) -> str:  # type: ignore[no-untyped-def]
        public_count = session.scalar(
            select(func.count(Test.id))
            .join(DataSource)
            .where(DataSource.key == "SCL_PUBLIC", Test.status == "active")
        )
        return "SCL_PUBLIC" if public_count else "DEMO"

    @staticmethod
    def _to_info(variant: TestVariant) -> TestInfo:
        test = variant.test
        source = test.data_source
        updated = variant.last_seen_at.date().isoformat()
        is_demo = source.key == "DEMO"
        specimen = variant.specimen.canonical_name if variant.specimen else "정보 없음"
        method = variant.method.name if variant.method else "정보 없음"
        return TestInfo(
            code=test.source_test_code,
            variant_key=variant.source_row_key,
            name=variant.display_name,
            aliases=[item.alias for item in test.aliases],
            specimen=specimen,
            container=variant.container_text,
            method=method,
            schedule=variant.schedule_text or "정보 없음",
            tat=variant.tat_text or "정보 없음",
            source_title="SCL 검사항목조회" if not is_demo else "SCL 검사항목 조회 시연 데이터",
            source_url=variant.detail_url,
            updated_at=updated,
            demo=is_demo,
            public_details=DatabaseCatalog._public_details(variant),
        )

    @staticmethod
    def _public_details(variant: TestVariant) -> dict[str, str]:
        detail = variant.public_detail
        values = {}
        if detail is not None and detail.fetch_status == "completed":
            values = dict(detail.fields_json)
            values.update({f"용기 {key}": value for key, value in detail.container_json.items()})
        codes = sorted({link.billing_code.code for link in variant.billing_codes})
        if codes:
            values["급여코드"] = ", ".join(codes)
        elif not values.get("급여코드") and variant.billing_code_text:
            values["급여코드"] = variant.billing_code_text
        return values

    @staticmethod
    def _prompt_details(details: dict[str, str], max_chars: int = 1400) -> dict[str, str]:
        selected: dict[str, str] = {}
        used = 0
        for key, value in details.items():
            if key in {"검사명", "SCL 검사코드", "검사방법", "검체명", "검사요일", "검사소요일"}:
                continue
            clipped = value[:500]
            remaining = max_chars - used - len(key) - 2
            if remaining <= 0:
                break
            selected[key] = clipped[:remaining]
            used += len(key) + len(selected[key]) + 2
        return selected

    def _seed_demo_if_empty(self) -> None:
        with self.session_factory.begin() as session:
            if session.scalar(select(func.count(Test.id))):
                return
            source = DataSource(
                key="DEMO",
                name="SCL 검사 시연 데이터",
                base_url="https://www.scllab.co.kr",
                source_type="fixture",
            )
            session.add(source)
            session.flush()

            method_cache: dict[str, Method] = {}
            specimen_cache: dict[str, Specimen] = {}
            raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
            now = utcnow()
            for item in raw:
                method_key = normalize_search_text(item["method"])
                method = method_cache.get(method_key)
                if method is None:
                    method = Method(name=item["method"], normalized_name=method_key)
                    session.add(method)
                    session.flush()
                    method_cache[method_key] = method

                specimen_key = normalize_search_text(item["specimen"])
                specimen = specimen_cache.get(specimen_key)
                if specimen is None:
                    specimen = Specimen(
                        canonical_name=item["specimen"],
                        normalized_name=specimen_key,
                    )
                    session.add(specimen)
                    session.flush()
                    specimen_cache[specimen_key] = specimen

                test = Test(
                    data_source_id=source.id,
                    source_test_code=item["code"],
                    name=item["name"],
                    normalized_name=normalize_search_text(item["name"]),
                    first_seen_at=now,
                    last_seen_at=now,
                )
                session.add(test)
                session.flush()
                for alias in item.get("aliases", []):
                    session.add(
                        TestAlias(
                            test_id=test.id,
                            alias=alias,
                            normalized_alias=normalize_search_text(alias),
                            alias_type="fixture",
                            source="fixture",
                        )
                    )
                tat_min, tat_max = parse_turnaround_days(item["tat"])
                days, shift = parse_schedule(item["schedule"])
                session.add(
                    TestVariant(
                        test_id=test.id,
                        source_sample_code="DEMO",
                        source_row_key=f"{item['code']}:DEMO",
                        display_name=item["name"],
                        method_id=method.id,
                        specimen_id=specimen.id,
                        container_text=item.get("container"),
                        schedule_text=item["schedule"],
                        schedule_days=days,
                        schedule_shift=shift,
                        tat_text=item["tat"],
                        tat_min_days=tat_min,
                        tat_max_days=tat_max,
                        detail_url=item["source_url"],
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                )


catalog = DatabaseCatalog()
