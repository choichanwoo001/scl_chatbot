import httpx
from app.config import Settings
from app.scl_public_data import SCLPublicDataSync, canonical_content_url


def _client(pages: dict[str, str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        html = pages.get(str(request.url))
        return httpx.Response(200 if html is not None else 404, text=html or "not found")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _sync() -> SCLPublicDataSync:
    return SCLPublicDataSync(
        Settings(openai_api_key=None, scl_crawl_delay_seconds=0),
    )


def test_board_table_parser_fetches_detail_body_and_attachment() -> None:
    list_url = "https://www.scllab.co.kr/front/bbsList.do?bbsId=BBS_TEST&webMode=FRONT&pageIndex=1"
    detail_url = "https://www.scllab.co.kr/front/viewAritcle.do?bbsId=BBS_TEST&nttId=123"
    pages = {
        list_url: """
            <p class='listCnt'><strong>1</strong> / 1 [ 총 <strong>1</strong> 건 ]</p>
            <table class='listTable'><tr>
              <td>1</td><td><a onclick=\"fnViewArticle('123')\">설 연휴 검사 안내</a></td>
              <td>2026-02-01</td>
            </tr></table>
        """,
        detail_url: """
            <table class='wtable'><tr><th>설 연휴 검사일정 안내</th></tr></table>
            <div class='txtbox'>연휴 기간에는 검사 일정이 변경됩니다.</div>
            <a class='btnDown' href=\"javascript:fnCommonDownFile('ATCH_1','0')\">일정.pdf</a>
        """,
    }

    with _client(pages) as client:
        fetched_pages, documents = _sync()._fetch_board(client, "BBS_TEST", "official_notice")

    assert len(fetched_pages) == 2
    assert len(documents) == 1
    document = documents[0]
    assert document["source_external_key"] == "BBS_TEST:123"
    assert document["title"] == "설 연휴 검사일정 안내"
    assert document["published_at"] == "2026-02-01"
    assert "검사 일정이 변경" in document["body_text"]
    assert document["attachments"] == [
        {
            "source_external_key": "ATCH_1:0",
            "file_name": "일정.pdf",
            "file_type": "pdf",
            "download_url": "https://www.scllab.co.kr/front/fms/FileDown.do",
            "preview_url": None,
        }
    ]


def test_board_card_parser_builds_stable_record_and_file_metadata() -> None:
    list_url = "https://www.scllab.co.kr/front/bbsList.do?bbsId=BBS_CARD&webMode=FRONT&pageIndex=1"
    pages = {
        list_url: """
            <ul class='leafletList_news'><li>
              <a href='https://www.scllab.co.kr/front/card?nttId=321'>
                <span class='tit'><span>건강 리플릿</span></span>
                <img src='/images/card.jpg'>
              </a>
              <div id='originContent'>건강 정보 본문</div>
              <button data-file='FILE_999'>다운로드</button>
            </li></ul>
        """,
    }

    with _client(pages) as client:
        fetched_pages, documents = _sync()._fetch_board(client, "BBS_CARD", "newsletter")

    assert len(fetched_pages) == 1
    assert len(documents) == 1
    document = documents[0]
    assert document["source_external_key"].startswith("BBS_CARD:")
    assert document["title"] == "건강 리플릿"
    assert document["body_text"] == "건강 정보 본문"
    assert document["thumbnail_url"] == "https://www.scllab.co.kr/images/card.jpg"
    assert document["attachments"][0]["source_external_key"] == "FILE_999:0"


def test_content_url_canonicalization_upgrades_scl_and_rejects_unknown_hosts() -> None:
    fallback = "https://www.scllab.co.kr/front/bbsList.do?bbsId=BBS_CARD"
    assert canonical_content_url("http://www.scllab.co.kr/front/card", fallback) == (
        "https://www.scllab.co.kr/front/card"
    )
    assert canonical_content_url("https://youtu.be/example", fallback) == "https://youtu.be/example"
    assert canonical_content_url("http://localhost:8080/front/card", fallback) == fallback
    assert canonical_content_url("https://evil.example/front/card", fallback) == fallback
