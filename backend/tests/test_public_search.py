from app.database import SessionLocal
from app.models import (
    AttachmentChunk,
    AttachmentContent,
    DataSource,
    DocumentAttachment,
    FAQCandidate,
    PublicDocument,
    ServiceLocation,
    SiteRoute,
)
from app.normalization import normalize_search_text
from app.public_search import public_search


def _seed_public_search_rows() -> None:
    with SessionLocal.begin() as session:
        source = session.query(DataSource).filter_by(key="SEARCH_TEST").first()
        if source is None:
            source = DataSource(key="SEARCH_TEST", name="search test", base_url="https://example.com")
            session.add(source)
            session.flush()
        if not session.query(SiteRoute).filter_by(path="/test/request-form").first():
            session.add(
                SiteRoute(
                    data_source_id=source.id,
                    path="/test/request-form",
                    label="검사의뢰서",
                    normalized_label=normalize_search_text("검사의뢰서"),
                    source_url="https://example.com/sitemap",
                    content_hash="a" * 64,
                )
            )
        if not session.query(ServiceLocation).filter_by(source_external_key="search-daegu").first():
            session.add(
                ServiceLocation(
                    data_source_id=source.id,
                    source_external_key="search-daegu",
                    name="대구검사센터",
                    region="대구",
                    address="대구광역시 테스트로 1",
                    phone="053-000-0000",
                    source_url="https://example.com/location",
                    content_hash="b" * 64,
                )
            )
        if not session.query(PublicDocument).filter_by(source_external_key="search-notice").first():
            session.add(
                PublicDocument(
                    data_source_id=source.id,
                    source_external_key="search-notice",
                    document_type="official_notice",
                    title="추석 연휴 검사일정 변경 안내",
                    normalized_title=normalize_search_text("추석 연휴 검사일정 변경 안내"),
                    body_text="연휴 기간 검사 일정이 변경됩니다.",
                    source_url="https://example.com/notice",
                    content_hash="c" * 64,
                )
            )


def test_search_prioritizes_location_for_contact_question() -> None:
    _seed_public_search_rows()
    hits = public_search.search("대구검사센터 전화번호", limit=3)
    assert hits
    assert hits[0].entity_type == "location"
    assert hits[0].metadata["phone"] == "053-000-0000"


def test_search_prioritizes_notice_for_schedule_question() -> None:
    _seed_public_search_rows()
    hits = public_search.search("추석 연휴 검사일정 변경 공문", limit=3)
    assert hits
    assert hits[0].entity_type == "document"


def test_search_prioritizes_official_faq_for_matching_test_question() -> None:
    _seed_public_search_rows()
    with SessionLocal.begin() as session:
        source = session.query(DataSource).filter_by(key="SEARCH_TEST").one()
        session.add(
            PublicDocument(
                data_source_id=source.id,
                source_external_key="official-faq-osteoporosis",
                document_type="official_faq",
                board_id="BBS_0014",
                title="골다공증 관련 검사는 어떤 검사가 있나요?",
                normalized_title=normalize_search_text("골다공증 관련 검사는 어떤 검사가 있나요?"),
                body_text="골 형성 표지자와 골 흡수 표지자가 있습니다.",
                source_url="https://www.scllab.co.kr/front/bbsList.do?bbsId=BBS_0014&pageIndex=1",
                content_hash="d" * 64,
            )
        )
        session.add(
            PublicDocument(
                data_source_id=source.id,
                source_external_key="general-osteoporosis-document",
                document_type="newsletter",
                title="골다공증 검사 건강자료",
                normalized_title=normalize_search_text("골다공증 검사 건강자료"),
                body_text="일반적인 건강 정보입니다.",
                source_url="https://example.com/general",
                content_hash="e" * 64,
            )
        )

    hits = public_search.search("골다공증 관련 검사는 어떤 검사 있나요?", ["document"], 3)

    assert hits
    assert hits[0].metadata["document_type"] == "official_faq"
    assert "골 형성 표지자" in (hits[0].snippet or "")
    assert all("장티푸스" not in hit.title for hit in hits)


def test_search_can_filter_to_routes() -> None:
    _seed_public_search_rows()
    hits = public_search.search("검사의뢰서 페이지", types=["route"], limit=3)
    assert hits[0].entity_type == "route"
    assert hits[0].metadata["path"] == "/test/request-form"


def test_detail_does_not_expose_inactive_public_data() -> None:
    with SessionLocal.begin() as session:
        source = session.query(DataSource).filter_by(key="SEARCH_TEST").first()
        if source is None:
            source = DataSource(key="SEARCH_TEST", name="search test", base_url="https://example.com")
            session.add(source)
            session.flush()
        location = ServiceLocation(
            data_source_id=source.id,
            source_external_key="inactive-location",
            name="폐쇄센터",
            region="서울",
            source_url="https://example.com/inactive",
            content_hash="f" * 64,
            status="inactive",
        )
        session.add(location)
        session.flush()
        location_id = location.id

    assert public_search.get("location", str(location_id)) is None


def test_searches_published_faq_and_extracted_attachment_text() -> None:
    _seed_public_search_rows()
    with SessionLocal.begin() as session:
        document = session.query(PublicDocument).filter_by(source_external_key="search-notice").one()
        attachment = DocumentAttachment(
            document_id=document.id,
            source_external_key="search-file:0",
            file_name="연휴 상세일정.pdf",
            file_type="pdf",
            download_url="https://www.scllab.co.kr/front/fms/FileDown.do",
        )
        session.add(attachment)
        session.flush()
        content = AttachmentContent(
            attachment_id=attachment.id,
            extraction_status="extracted",
            extractor="test",
            extracted_text="광복절 검사 접수는 오후 3시에 마감됩니다.",
            normalized_text=normalize_search_text("광복절 검사 접수는 오후 3시에 마감됩니다."),
            char_count=24,
        )
        session.add(content)
        session.flush()
        session.add(
            AttachmentChunk(
                attachment_content_id=content.id,
                sequence=0,
                page_number=1,
                section_label="1쪽",
                text="광복절 검사 접수는 오후 3시에 마감됩니다.",
                normalized_text=normalize_search_text("광복절 검사 접수는 오후 3시에 마감됩니다."),
            )
        )
        faq = FAQCandidate(
            fingerprint="f" * 64,
            canonical_question="검사결과는 어디서 확인하나요?",
            normalized_question=normalize_search_text("검사결과는 어디서 확인하나요?"),
            canonical_answer="결과조회 메뉴에서 본인인증 후 확인합니다.",
            domain="result",
            sub_intent="navigate_to_result",
            source_refs=[],
            status="published",
        )
        session.add(faq)

    attachment_hits = public_search.search("광복절 오후 3시 접수 마감", ["attachment"], 3)
    faq_hits = public_search.search("검사결과 어디서 확인", ["faq"], 3)

    assert attachment_hits and attachment_hits[0].entity_type == "attachment"
    assert attachment_hits[0].metadata["page_number"] == 1
    assert faq_hits and faq_hits[0].entity_type == "faq"


def test_generic_request_form_download_prefers_general_scl_form() -> None:
    _seed_public_search_rows()
    with SessionLocal.begin() as session:
        document = session.query(PublicDocument).filter_by(source_external_key="search-notice").one()
        for index, (file_name, body) in enumerate(
            [
                ("전문의뢰서.pdf", "Oligoclonal band 검사의뢰서"),
                ("[의뢰서] 일반검사의뢰서.pdf", "SCL 일반 검사의뢰서"),
            ]
        ):
            attachment = DocumentAttachment(
                document_id=document.id,
                source_external_key=f"request-form:{index}",
                file_name=file_name,
                file_type="pdf",
                download_url=f"https://www.scllab.co.kr/forms/{index}.pdf",
            )
            session.add(attachment)
            session.flush()
            content = AttachmentContent(
                attachment_id=attachment.id,
                extraction_status="extracted",
                extractor="test",
                extracted_text=body,
                normalized_text=normalize_search_text(body),
                char_count=len(body),
            )
            session.add(content)
            session.flush()
            session.add(
                AttachmentChunk(
                    attachment_content_id=content.id,
                    sequence=0,
                    page_number=1,
                    section_label="1쪽",
                    text=body,
                    normalized_text=normalize_search_text(body),
                )
            )

    hits = public_search.search("검사의뢰서 다운로드", ["attachment"], 3)

    assert hits
    assert "일반검사의뢰서" in hits[0].title
