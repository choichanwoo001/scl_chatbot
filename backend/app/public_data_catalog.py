from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .models import (
    PreservativeGuide,
    ServiceLocation,
    SiteRoute,
    TaxonomyTerm,
)
from .normalization import clean_text, normalize_search_text
from .public_data_common import SyncResult
from .scl_crawler import SCL_BASE_URL


class PublicCatalogSync:
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
