from __future__ import annotations

import hashlib
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .normalization import clean_text, normalize_search_text
from .public_data_common import SyncResult
from .scl_crawler import SCL_BASE_URL


class PublicDocumentSync:
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
