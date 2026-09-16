from __future__ import annotations

import hashlib
import re
import time
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .normalization import clean_text, normalize_search_text
from .public_data_common import canonical_content_url
from .scl_crawler import SCL_BASE_URL


class PublicDataTransport:
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
                fingerprint = hashlib.sha256(normalize_search_text(question).encode("utf-8")).hexdigest()[:24]
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
