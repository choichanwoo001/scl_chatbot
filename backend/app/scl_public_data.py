from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select

from .config import Settings
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
    ServiceLocation,
    SiteRoute,
    SourcePage,
    TaxonomyTerm,
    utcnow,
)
from .normalization import changed_fields, clean_text, normalize_search_text
from .repositories.sync_runs import SyncRunRepository
from .scl_crawler import SCL_BASE_URL, SCL_SOURCE_KEY


@dataclass(frozen=True)
class SyncResult:
    dataset: str
    run_id: int
    pages: int
    records: int
    inserted: int
    updated: int
    unchanged: int
    deactivated: int


def stable_hash(payload: dict[str, Any]) -> str:
    value = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


ALLOWED_EXTERNAL_CONTENT_HOSTS = frozenset(
    {
        "www.youtube.com",
        "youtube.com",
        "youtu.be",
        "blog.naver.com",
        "happybean.naver.com",
    }
)


def canonical_content_url(value: str | None, fallback: str) -> str:
    """Keep known public content links and prevent local/unknown URLs entering the DB."""
    if not value:
        return fallback
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    if host == "scllab.co.kr" or host.endswith(".scllab.co.kr"):
        return urlunparse(parsed._replace(scheme="https"))
    if parsed.scheme == "https" and host in ALLOWED_EXTERNAL_CONTENT_HOSTS:
        return value
    return fallback


class SCLPublicDataSync:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client
        self._runs = SyncRunRepository(SCL_SOURCE_KEY, "SCL 공개 홈페이지", SCL_BASE_URL)

    def sync_containers(self) -> SyncResult:
        dataset = "containers"
        run_id = self._start_run(dataset)
        try:
            pages = self._fetch_paginated("/front/contents/contents_tub.do", ".sampleList > li")
            parsed: list[dict[str, Any]] = []
            for _page_number, url, html in pages:
                soup = BeautifulSoup(html, "html.parser")
                for node in soup.select(".sampleList > li"):
                    title = clean_text(node.select_one(".tit").get_text(" ", strip=True))
                    match = re.match(r"(\d+)\.\s*(.*)", title)
                    if not match:
                        continue
                    fields = {
                        clean_text(item.select_one(".key").get_text(" ", strip=True)).replace(
                            "/ ", "/"
                        ): clean_text(item.select_one(".value").get_text(" ", strip=True))
                        for item in node.select(".checkInfoList > ul > li")
                        if item.select_one(".key") and item.select_one(".value")
                    }
                    image = node.select_one("img.listImg")
                    payload = {
                        "source_external_key": match.group(1),
                        "name": match.group(2),
                        "additive": fields.get("첨가제"),
                        "major_tests_text": fields.get("주요검사항목"),
                        "collection_volume_text": fields.get("채취량"),
                        "storage_text": fields.get("보관"),
                        "caution_text": fields.get("주의사항/ 참고") or fields.get("주의사항/참고"),
                        "image_url": image.get("src") if image else None,
                        "source_url": url,
                    }
                    parsed.append(payload)
            return self._store_containers(run_id, pages, parsed)
        except Exception as error:
            self._fail_run(run_id, error)
            raise

    def sync_notices(self, board_id: str = "BBS_0004") -> SyncResult:
        dataset = "notices"
        run_id = self._start_run(dataset)
        pages: list[tuple[int, str, str]] = []
        try:
            owned_client = self._client is None
            client = self._client or httpx.Client(
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": "SCLChatPrototype/0.1 (public data sync)"},
            )
            try:
                first_url = (
                    f"{SCL_BASE_URL}/front/bbsList.do?bbsId={board_id}&webMode=FRONT&searchOp6=&pageIndex=1"
                )
                first = self._get(client, first_url)
                first_soup = BeautifulSoup(first, "html.parser")
                count = clean_text(first_soup.select_one(".listCnt").get_text(" ", strip=True))
                match = re.search(r"/\s*(\d+)\s*\[", count)
                if not match:
                    raise ValueError(f"Unexpected notice page count: {count}")
                total_pages = int(match.group(1))
                list_pages = [(1, first_url, first)]
                for page_number in range(2, total_pages + 1):
                    self._delay()
                    url = (
                        f"{SCL_BASE_URL}/front/bbsList.do?bbsId={board_id}"
                        f"&webMode=FRONT&searchOp6=&pageIndex={page_number}"
                    )
                    list_pages.append((page_number, url, self._get(client, url)))

                summaries: list[dict[str, Any]] = []
                for page_number, _, html in list_pages:
                    soup = BeautifulSoup(html, "html.parser")
                    for row in soup.select("table.listTable tr"):
                        link = row.select_one("a[onclick*='fnViewArticle']")
                        cells = row.select("td")
                        if not link or len(cells) < 4:
                            continue
                        key_match = re.search(r"fnViewArticle\('([^']+)'", link.get("onclick", ""))
                        if not key_match:
                            continue
                        summaries.append(
                            {
                                "ntt_id": key_match.group(1),
                                "title": clean_text(link.get_text(" ", strip=True)),
                                "published": clean_text(cells[3].get_text(" ", strip=True)),
                                "list_page": page_number,
                            }
                        )

                documents: list[dict[str, Any]] = []
                for index, summary in enumerate(summaries, 1):
                    self._delay()
                    ntt_id = summary["ntt_id"]
                    url = f"{SCL_BASE_URL}/front/viewAritcle.do?bbsId={board_id}&nttId={ntt_id}"
                    html = self._get(client, url)
                    pages.append((total_pages + index, url, html))
                    soup = BeautifulSoup(html, "html.parser")
                    title_node = soup.select_one("table.wtable th")
                    body_nodes = soup.select(".txtbox")
                    title = (
                        clean_text(title_node.get_text(" ", strip=True)) if title_node else summary["title"]
                    )
                    body = clean_text(" ".join(node.get_text(" ", strip=True) for node in body_nodes))
                    attachments: list[dict[str, str]] = []
                    for file_link in soup.select("a.btnDown[href*='fnCommonDownFile']"):
                        args = re.findall(r"'([^']*)'", file_link.get("href", ""))
                        if len(args) < 2:
                            continue
                        atch_id, file_sn = args[:2]
                        file_name = clean_text(file_link.get_text(" ", strip=True)) or f"{atch_id}-{file_sn}"
                        attachments.append(
                            {
                                "source_external_key": f"{atch_id}:{file_sn}",
                                "file_name": file_name,
                                "file_type": file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "",
                                "download_url": f"{SCL_BASE_URL}/front/fms/FileDown.do",
                                "preview_url": (
                                    f"{SCL_BASE_URL}/front/fms/imageSrc.do?atchFileId={atch_id}"
                                    f"&fileSn={file_sn}&bIdx={ntt_id}"
                                ),
                            }
                        )
                    documents.append(
                        {
                            "source_external_key": f"{board_id}:{ntt_id}",
                            "document_type": "official_notice",
                            "board_id": board_id,
                            "title": title,
                            "summary": body[:500] or None,
                            "body_text": body or None,
                            "published_at": summary["published"],
                            "thumbnail_url": None,
                            "source_url": url,
                            "attachments": attachments,
                        }
                    )
                pages = list_pages + pages
            finally:
                if owned_client:
                    client.close()
            return self._store_documents(run_id, dataset, pages, documents)
        except Exception as error:
            self._fail_run(run_id, error)
            raise

    def sync_preservatives(self) -> SyncResult:
        dataset = "preservatives"
        run_id = self._start_run(dataset)
        url = f"{SCL_BASE_URL}/front/check/preservative.do"
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                html = self._get(client, url)
            soup = BeautifulSoup(html, "html.parser")
            payloads: list[dict[str, Any]] = []
            for row in soup.select("table.table_list tr"):
                cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.select("td")]
                if len(cells) != 7:
                    continue
                payloads.append(
                    dict(
                        test_name=cells[0],
                        light_protection=cells[1],
                        acetic_acid_50=cells[2],
                        hcl_6n=cells[3],
                        boric_acid_10g=cells[4],
                        sodium_carbonate_5g=cells[5],
                        no_preservative=cells[6],
                        source_url=url,
                    )
                )
            return self._store_simple(
                run_id,
                dataset,
                [(1, url, html)],
                payloads,
                PreservativeGuide,
                "normalized_test_name",
                lambda p: normalize_search_text(str(p["test_name"])),
            )
        except Exception as error:
            self._fail_run(run_id, error)
            raise

    def sync_resources(self) -> SyncResult:
        dataset = "resources"
        run_id = self._start_run(dataset)
        try:
            forms = self._fetch_paginated("/front/contents/contents_req.do", ".certificationList > li")
            leaflets = self._fetch_paginated("/front/contents/contents_doc.do", ".inspectionList > li")
            payloads: list[dict[str, Any]] = []
            for _, url, html in forms:
                soup = BeautifulSoup(html, "html.parser")
                for _index, node in enumerate(soup.select(".certificationList > li"), 1):
                    title = re.sub(
                        r"^\d+\.\s*", "", clean_text(node.select_one(".subj").get_text(" ", strip=True))
                    )
                    action = (node.select_one("[onclick*='fnOpenFile']") or {}).get("onclick", "")
                    file_match = re.search(r"fnOpenFile\('([^']+)'", action)
                    file_url = file_match.group(1) if file_match else ""
                    image = node.select_one("img.listImg")
                    key = (
                        hashlib.sha256(file_url.encode()).hexdigest()[:20]
                        if file_url
                        else normalize_search_text(title)
                    )
                    payloads.append(
                        self._resource_payload(
                            "request_form",
                            key,
                            title,
                            None,
                            None,
                            url,
                            image.get("src") if image else None,
                            file_url,
                        )
                    )
            for _, url, html in leaflets:
                soup = BeautifulSoup(html, "html.parser")
                for node in soup.select(".inspectionList > li"):
                    title_node = node.select_one(".insp01_1")
                    if not title_node:
                        continue
                    title = clean_text(title_node.get_text(" ", strip=True))
                    summary = (
                        clean_text(node.select_one(".insp03").get_text(" ", strip=True))
                        if node.select_one(".insp03")
                        else None
                    )
                    published = (
                        clean_text(node.select_one(".insp02").get_text(" ", strip=True))
                        if node.select_one(".insp02")
                        else None
                    )
                    action_node = node.select_one("[onclick*='fnOpenFile']")
                    file_match = re.search(
                        r"fnOpenFile\('([^']+)'", action_node.get("onclick", "") if action_node else ""
                    )
                    file_url = file_match.group(1) if file_match else ""
                    image = node.select_one(".pic img")
                    key = (
                        hashlib.sha256(file_url.encode()).hexdigest()[:20]
                        if file_url
                        else normalize_search_text(title)
                    )
                    payloads.append(
                        self._resource_payload(
                            "academic_leaflet",
                            key,
                            title,
                            summary,
                            published,
                            url,
                            image.get("src") if image else None,
                            file_url,
                        )
                    )
            return self._store_documents(run_id, dataset, forms + leaflets, payloads)
        except Exception as error:
            self._fail_run(run_id, error)
            raise

    @staticmethod
    def _resource_payload(
        kind: str,
        key: str,
        title: str,
        summary: str | None,
        published: str | None,
        source_url: str,
        thumbnail: str | None,
        file_url: str,
    ) -> dict[str, Any]:
        file_name = file_url.rsplit("/", 1)[-1] if file_url else title
        return {
            "source_external_key": f"{kind}:{key}",
            "document_type": kind,
            "board_id": None,
            "title": title,
            "summary": summary,
            "body_text": summary,
            "published_at": published,
            "thumbnail_url": thumbnail,
            "source_url": source_url,
            "attachments": [
                {
                    "source_external_key": key,
                    "file_name": file_name,
                    "file_type": file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "",
                    "download_url": file_url,
                    "preview_url": file_url,
                }
            ]
            if file_url
            else [],
        }

    def sync_taxonomy(self) -> SyncResult:
        dataset = "taxonomy"
        run_id = self._start_run(dataset)
        url = f"{SCL_BASE_URL}/front/check/check_team_tab01.do"
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                html = self._get(client, url)
            soup = BeautifulSoup(html, "html.parser")
            # These are the source page's laboratory-category navigation labels.
            labels = []
            for text in soup.stripped_strings:
                value = clean_text(str(text))
                if (
                    value
                    in {
                        "자동화 운영",
                        "진단혈액",
                        "분자진단",
                        "임상미생물",
                        "진단면역",
                        "특수분석",
                        "세포유전",
                        "세포병리",
                        "조직병리",
                        "휴먼지놈",
                        "마이지놈",
                        "특수미생물분석",
                        "EQA자원개발",
                    }
                    and value not in labels
                ):
                    labels.append(value)
            payloads = [
                {"taxonomy": "laboratory_team", "name": name, "description": None, "source_url": url}
                for name in labels
            ]
            return self._store_simple(
                run_id,
                dataset,
                [(1, url, html)],
                payloads,
                TaxonomyTerm,
                "normalized_name",
                lambda p: normalize_search_text(str(p["name"])),
                scope_column="taxonomy",
                scope_value="laboratory_team",
            )
        except Exception as error:
            self._fail_run(run_id, error)
            raise

    def sync_locations(self) -> SyncResult:
        dataset = "locations"
        run_id = self._start_run(dataset)
        url = f"{SCL_BASE_URL}/front/about/network_list.do"
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                page_html = self._get(client, url)
                response = client.post(
                    f"{SCL_BASE_URL}/front/about/callBackSidoList.do",
                    data={"searchOp1": "", "sido": "", "gugun": ""},
                )
                response.raise_for_status()
                tag = response.json()["resultMap"]["tag"]
                center_pages = []
                for center_number in range(1, 4):
                    center_url = f"{SCL_BASE_URL}/front/about/center{center_number}.do"
                    center_pages.append((center_number + 2, center_url, self._get(client, center_url)))
            soup = BeautifulSoup(tag, "html.parser")
            names = [clean_text(node.get_text(" ", strip=True)) for node in soup.select("a.infoLi")]
            payloads = []
            for index, row in enumerate(soup.select("tr.infoTr")):
                cells = row.select("td")
                if len(cells) < 3:
                    continue
                phones = [clean_text(p.get_text(" ", strip=True)) for p in cells[1].select("p")]
                address = clean_text(cells[2].get_text(" ", strip=True))
                payloads.append(
                    {
                        "source_external_key": str(index),
                        "location_type": "branch",
                        "name": names[index] if index < len(names) else f"branch-{index}",
                        "region": address.split(" ", 1)[0] if address else None,
                        "address": address,
                        "phone": phones[0] if phones else None,
                        "fax": phones[1] if len(phones) > 1 else None,
                        "latitude": None,
                        "longitude": None,
                        "source_url": url,
                    }
                )
            for center_number, center_url, center_html in center_pages:
                center = BeautifulSoup(center_html, "html.parser")
                name_node = center.select_one(".tabList li.on a")
                details = {
                    clean_text(dt.get_text(" ", strip=True)): clean_text(
                        dt.find_next_sibling("dd").get_text(" ", strip=True)
                    )
                    for dt in center.select("dl.box dt")
                    if dt.find_next_sibling("dd")
                }
                name = (
                    clean_text(name_node.get_text(" ", strip=True))
                    if name_node
                    else f"지역검사센터 {center_number - 2}"
                )
                address = details.get("주소")
                payloads.append(
                    {
                        "source_external_key": f"center:{center_number - 2}",
                        "location_type": "regional_center",
                        "name": name,
                        "region": address.split(" ", 1)[0] if address else None,
                        "address": address,
                        "phone": details.get("전화번호"),
                        "fax": None,
                        "latitude": None,
                        "longitude": None,
                        "source_url": center_url,
                    }
                )
            return self._store_simple(
                run_id,
                dataset,
                [(1, url, page_html), (2, url + "#callback", tag)] + center_pages,
                payloads,
                ServiceLocation,
                "source_external_key",
                lambda p: str(p["source_external_key"]),
            )
        except Exception as error:
            self._fail_run(run_id, error)
            raise

    def sync_routes(self) -> SyncResult:
        dataset = "routes"
        run_id = self._start_run(dataset)
        url = f"{SCL_BASE_URL}/front/etc/sitemap.do"
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                html = self._get(client, url)
            soup = BeautifulSoup(html, "html.parser")
            payloads: list[dict[str, Any]] = []
            parent_path: str | None = None
            for order, node in enumerate(soup.select(".sitemapBox a")):
                raw_label = clean_text(node.get_text(" ", strip=True))
                depth = 1 if raw_label.startswith("-") else 0
                label = raw_label.lstrip("- ")
                action = " ".join([node.get("href", ""), node.get("onclick", "") or ""])
                path_match = re.search(r"'(/front/[^']+)'", action)
                href = node.get("href", "")
                path = path_match.group(1) if path_match else href if href.startswith(("/", "http")) else ""
                board_match = re.search(r"'(BBS_\d+)'", action)
                if board_match and "/front/bbsList.do" in action:
                    path = f"/front/bbsList.do?bbsId={board_match.group(1)}"
                if not path:
                    if depth == 0:
                        parent_path = None
                    continue
                if depth == 0:
                    parent_path = path
                payloads.append(
                    {
                        "path": path,
                        "label": label,
                        "parent_path": parent_path if depth else None,
                        "depth": depth,
                        "menu_order": order,
                        "source_url": url,
                    }
                )
            # The sitemap repeats a few destinations in footer-level groups; a route path is the canonical key.
            payloads = list({str(payload["path"]): payload for payload in payloads}.values())
            return self._store_simple(
                run_id, dataset, [(1, url, html)], payloads, SiteRoute, "path", lambda p: str(p["path"])
            )
        except Exception as error:
            self._fail_run(run_id, error)
            raise

    def sync_content(self) -> SyncResult:
        dataset = "content"
        run_id = self._start_run(dataset)
        boards = {
            "BBS_0001": "news",
            "BBS_0002": "press_column",
            "BBS_0030": "health_recipe",
            "BBS_0013": "newsletter",
            "BBS_0022": "health_information",
            "BBS_0024": "social_contribution",
        }
        try:
            all_pages: list[tuple[int, str, str]] = []
            all_documents: list[dict[str, Any]] = []
            with httpx.Client(
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": "SCLChatPrototype/0.1 (public data sync)"},
            ) as client:
                page_offset = 0
                for board_id, document_type in boards.items():
                    pages, documents = self._fetch_board(client, board_id, document_type, page_offset)
                    all_pages.extend(pages)
                    all_documents.extend(documents)
                    page_offset += len(pages)
            return self._store_documents(run_id, dataset, all_pages, all_documents)
        except Exception as error:
            self._fail_run(run_id, error)
            raise

    def sync_faqs(self, board_id: str = "BBS_0014") -> SyncResult:
        """Synchronize the official SCL FAQ board as first-party documents."""
        dataset = "faqs"
        run_id = self._start_run(dataset)
        try:
            owned_client = self._client is None
            client = self._client or httpx.Client(
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": "SCLChatPrototype/0.1 (public data sync)"},
            )
            try:
                pages, documents = self._fetch_faqs(client, board_id)
            finally:
                if owned_client:
                    client.close()
            return self._store_documents(run_id, dataset, pages, documents)
        except Exception as error:
            self._fail_run(run_id, error)
            raise

    def _fetch_faqs(
        self,
        client: httpx.Client,
        board_id: str,
    ) -> tuple[list[tuple[int, str, str]], list[dict[str, Any]]]:
        first_url = f"{SCL_BASE_URL}/front/bbsList.do?bbsId={board_id}&pageIndex=1"
        first_html = self._get(client, first_url)
        first_soup = BeautifulSoup(first_html, "html.parser")
        page_numbers = [
            int(text)
            for node in first_soup.select(".paging a")
            if (text := clean_text(node.get_text(" ", strip=True))).isdigit()
        ]
        total_pages = max(page_numbers, default=1)
        pages = [(1, first_url, first_html)]
        for page_number in range(2, total_pages + 1):
            self._delay()
            url = f"{SCL_BASE_URL}/front/bbsList.do?bbsId={board_id}&pageIndex={page_number}"
            html = self._get(client, url)
            if not BeautifulSoup(html, "html.parser").select("table.faqType tr.trq"):
                raise ValueError(f"No FAQ records on {url}")
            pages.append((page_number, url, html))

        documents: list[dict[str, Any]] = []
        for page_number, page_url, html in pages:
            soup = BeautifulSoup(html, "html.parser")
            for question_row in soup.select("table.faqType tr.trq"):
                question_node = question_row.select_one(".faqLink")
                answer_row = question_row.find_next_sibling("tr", class_="tra")
                answer_node = answer_row.select_one(".traDesc") if answer_row else None
                question = clean_text(question_node.get_text(" ", strip=True)) if question_node else ""
                answer = clean_text(answer_node.get_text(" ", strip=True)) if answer_node else ""
                if not question or not answer:
                    continue
                fingerprint = hashlib.sha256(
                    normalize_search_text(question).encode("utf-8")
                ).hexdigest()[:24]
                documents.append(
                    {
                        "source_external_key": f"{board_id}:faq:{fingerprint}",
                        "document_type": "official_faq",
                        "board_id": board_id,
                        "title": question,
                        "summary": answer[:500],
                        "body_text": answer,
                        "published_at": None,
                        "thumbnail_url": None,
                        "source_url": page_url,
                        "attachments": [],
                        "page_number": page_number,
                    }
                )
        return pages, documents

    def _fetch_board(
        self, client: httpx.Client, board_id: str, document_type: str, page_offset: int = 0
    ) -> tuple[list[tuple[int, str, str]], list[dict[str, Any]]]:
        base_list = f"{SCL_BASE_URL}/front/bbsList.do?bbsId={board_id}&webMode=FRONT&pageIndex="
        first_url = base_list + "1"
        first = self._get(client, first_url)
        soup = BeautifulSoup(first, "html.parser")
        count_node = soup.select_one(".listCnt")
        count_text = clean_text(count_node.get_text(" ", strip=True)) if count_node else ""
        match = re.search(r"/\s*(\d+)\s*\[", count_text)
        total_pages = int(match.group(1)) if match else 1
        list_pages = [(page_offset + 1, first_url, first)]
        for number in range(2, total_pages + 1):
            self._delay()
            url = base_list + str(number)
            list_pages.append((page_offset + number, url, self._get(client, url)))
        summaries = []
        for _, _, html in list_pages:
            current = BeautifulSoup(html, "html.parser")
            for row in current.select("table.listTable tr"):
                link = row.select_one("a[onclick*='fnViewArticle']")
                if not link:
                    continue
                key_match = re.search(r"fnViewArticle\('([^']+)'", link.get("onclick", ""))
                if not key_match:
                    continue
                values = [clean_text(cell.get_text(" ", strip=True)) for cell in row.select("td")]
                published = next(
                    (value for value in values if re.fullmatch(r"20\d\d-\d\d-\d\d", value)), None
                )
                summaries.append((key_match.group(1), clean_text(link.get_text(" ", strip=True)), published))
        if not summaries:
            cards: list[dict[str, Any]] = []
            for _, page_url, html in list_pages:
                current = BeautifulSoup(html, "html.parser")
                for node in current.select(".leafletList_news > li, .video_list li"):
                    title_node = node.select_one(".tit span") or node.select_one("a")
                    title = clean_text(title_node.get_text(" ", strip=True)) if title_node else ""
                    if not title:
                        continue
                    markup = str(node)
                    ntt_match = re.search(r"nttId=(\d+)", markup)
                    file_match = re.search(r"(FILE_\d+)", markup)
                    key_seed = (
                        ntt_match.group(1) if ntt_match else file_match.group(1) if file_match else title
                    )
                    # Some cards deliberately reuse the same file/article. Title is part of the source identity.
                    key = hashlib.sha256(f"{key_seed}:{title}".encode()).hexdigest()[:20]
                    source_key = f"{board_id}:{key}"
                    link = node.select_one("a[href^='http']")
                    source_url = canonical_content_url(link.get("href") if link else None, page_url)
                    body_node = node.select_one("#originContent")
                    body = clean_text(body_node.get_text(" ", strip=True)) if body_node else None
                    image = node.select_one("img[src]")
                    thumbnail = urljoin(SCL_BASE_URL, image.get("src")) if image else None
                    attachments = []
                    if file_match:
                        file_id = file_match.group(1)
                        attachments.append(
                            {
                                "source_external_key": f"{file_id}:0",
                                "file_name": f"{title}.pdf",
                                "file_type": "pdf",
                                "download_url": f"{SCL_BASE_URL}/front/fms/FileDown.do",
                                "preview_url": thumbnail,
                            }
                        )
                    cards.append(
                        {
                            "source_external_key": source_key,
                            "document_type": document_type,
                            "board_id": board_id,
                            "title": title,
                            "summary": (body[:500] if body else None),
                            "body_text": body,
                            "published_at": None,
                            "thumbnail_url": thumbnail,
                            "source_url": source_url,
                            "attachments": attachments,
                        }
                    )
            return list_pages, cards
        detail_pages: list[tuple[int, str, str]] = []
        documents = []
        for index, (ntt_id, fallback_title, published) in enumerate(summaries, 1):
            self._delay()
            url = f"{SCL_BASE_URL}/front/viewAritcle.do?bbsId={board_id}&nttId={ntt_id}"
            html = self._get(client, url)
            detail_pages.append((page_offset + total_pages + index, url, html))
            detail = BeautifulSoup(html, "html.parser")
            title_node = detail.select_one("table.wtable th")
            title = clean_text(title_node.get_text(" ", strip=True)) if title_node else fallback_title
            body = clean_text(" ".join(node.get_text(" ", strip=True) for node in detail.select(".txtbox")))
            attachments = []
            for file_link in detail.select("a.btnDown[href*='fnCommonDownFile']"):
                args = re.findall(r"'([^']*)'", file_link.get("href", ""))
                if len(args) < 2:
                    continue
                atch_id, file_sn = args[:2]
                file_name = clean_text(file_link.get_text(" ", strip=True)) or f"{atch_id}-{file_sn}"
                attachments.append(
                    {
                        "source_external_key": f"{atch_id}:{file_sn}",
                        "file_name": file_name,
                        "file_type": file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "",
                        "download_url": f"{SCL_BASE_URL}/front/fms/FileDown.do",
                        "preview_url": None,
                    }
                )
            documents.append(
                {
                    "source_external_key": f"{board_id}:{ntt_id}",
                    "document_type": document_type,
                    "board_id": board_id,
                    "title": title,
                    "summary": body[:500] or None,
                    "body_text": body or None,
                    "published_at": published,
                    "thumbnail_url": None,
                    "source_url": url,
                    "attachments": attachments,
                }
            )
        return list_pages + detail_pages, documents

    @staticmethod
    def _get(client: httpx.Client, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = client.get(url)
                response.raise_for_status()
                return response.text
            except httpx.HTTPError as error:
                last_error = error
                if attempt < 2:
                    time.sleep(attempt + 1)
        raise RuntimeError(f"Failed to fetch {url}: {last_error}")

    def _delay(self) -> None:
        if self.settings.scl_crawl_delay_seconds:
            time.sleep(self.settings.scl_crawl_delay_seconds)

    def _fetch_paginated(self, path: str, item_selector: str) -> list[tuple[int, str, str]]:
        owned_client = self._client is None
        client = self._client or httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "SCLChatPrototype/0.1 (public data sync)"},
        )
        try:
            first_url = f"{SCL_BASE_URL}{path}?pageIndex=1"
            response = client.get(first_url)
            response.raise_for_status()
            first_html = response.text
            soup = BeautifulSoup(first_html, "html.parser")
            page_numbers = [
                int(text)
                for node in soup.select(".paging a")
                if (text := clean_text(node.get_text(" ", strip=True))).isdigit()
            ]
            total_pages = max(page_numbers, default=1)
            pages = [(1, first_url, first_html)]
            for page_number in range(2, total_pages + 1):
                if self.settings.scl_crawl_delay_seconds:
                    time.sleep(self.settings.scl_crawl_delay_seconds)
                url = f"{SCL_BASE_URL}{path}?pageIndex={page_number}"
                current = client.get(url)
                current.raise_for_status()
                if not BeautifulSoup(current.text, "html.parser").select(item_selector):
                    raise ValueError(f"No {item_selector} records on {url}")
                pages.append((page_number, url, current.text))
            return pages
        finally:
            if owned_client:
                client.close()

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

    def _start_run(self, dataset: str) -> int:
        return self._runs.start(dataset)

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

    def _fail_run(self, run_id: int, error: Exception) -> None:
        self._runs.fail(run_id, error)
