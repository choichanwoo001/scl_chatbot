from __future__ import annotations

import json
import sys

from app.config import settings
from app.database import SessionLocal, engine
from app.database_transfer import TransferError, verify_constraints
from sqlalchemy import text

EXPECTED = {
    "containers": 53,
    "preservatives": 53,
    "resources": 151,
    "taxonomy": 13,
    "locations": 66,
    "routes": 56,
    "notices": 883,
    "content": 1024,
}


def main() -> None:
    failures: list[str] = []
    with SessionLocal() as session:
        scalar_checks = {
            "containers": "select count(*) from containers where status='active'",
            "preservatives": "select count(*) from preservative_guides where status='active'",
            "resources": "select count(*) from public_documents where status='active' and document_type in ('request_form','academic_leaflet')",
            "taxonomy": "select count(*) from taxonomy_terms where taxonomy='laboratory_team'",
            "locations": "select count(*) from service_locations where status='active'",
            "routes": "select count(*) from site_routes where status='active'",
            "notices": "select count(*) from public_documents where status='active' and document_type='official_notice'",
            "content": "select count(*) from public_documents where status='active' and document_type in ('news','press_column','health_recipe','newsletter','health_information','social_contribution')",
        }
        counts = {name: int(session.execute(text(sql)).scalar_one()) for name, sql in scalar_checks.items()}
        for name, expected in EXPECTED.items():
            if counts[name] != expected:
                failures.append(f"{name}: expected {expected}, got {counts[name]}")

        duplicate_checks = {
            "container_keys": "select count(*) from (select data_source_id,source_external_key from containers group by 1,2 having count(*)>1)",
            "document_keys": "select count(*) from (select data_source_id,source_external_key from public_documents group by 1,2 having count(*)>1)",
            "attachment_keys": "select count(*) from (select document_id,source_external_key from document_attachments group by 1,2 having count(*)>1)",
            "attachment_content_keys": "select count(*) from (select attachment_id from attachment_contents group by 1 having count(*)>1)",
            "location_keys": "select count(*) from (select data_source_id,source_external_key from service_locations group by 1,2 having count(*)>1)",
            "route_paths": "select count(*) from (select data_source_id,path from site_routes group by 1,2 having count(*)>1)",
        }
        duplicates = {
            name: int(session.execute(text(sql)).scalar_one()) for name, sql in duplicate_checks.items()
        }
        for name, count in duplicates.items():
            if count:
                failures.append(f"{name}: {count} duplicate groups")

        required_checks = {
            "empty_container_names": "select count(*) from containers where trim(name)=''",
            "empty_document_titles": "select count(*) from public_documents where trim(title)=''",
            "empty_location_names": "select count(*) from service_locations where trim(name)=''",
            "empty_route_labels": "select count(*) from site_routes where trim(label)='' or trim(path)=''",
            "orphan_attachments": "select count(*) from document_attachments a left join public_documents d on d.id=a.document_id where d.id is null",
            "orphan_mentions": "select count(*) from container_test_mentions m left join containers c on c.id=m.container_id where c.id is null",
            "orphan_test_taxonomy_links": "select count(*) from test_taxonomy_links l left join tests t on t.id=l.test_id left join taxonomy_terms x on x.id=l.taxonomy_term_id where t.id is null or x.id is null",
            "mojibake_document_text": "select count(*) from public_documents where title like '%�%' or coalesce(summary,'') like '%�%' or coalesce(body_text,'') like '%�%'",
            "mojibake_container_text": "select count(*) from containers where name like '%�%' or coalesce(major_tests_text,'') like '%�%'",
            "mojibake_location_text": "select count(*) from service_locations where name like '%�%' or coalesce(address,'') like '%�%'",
            "invalid_document_urls": """select count(*) from public_documents where source_url is not null and not (
                source_url like 'https://%.scllab.co.kr/%'
                or source_url like 'https://scllab.co.kr/%'
                or source_url like 'https://www.youtube.com/%'
                or source_url like 'https://youtube.com/%'
                or source_url like 'https://youtu.be/%'
                or source_url like 'https://blog.naver.com/%'
                or source_url like 'https://happybean.naver.com/%'
            )""",
            "invalid_location_urls": "select count(*) from service_locations where source_url is not null and source_url not like 'https://www.scllab.co.kr/%'",
            "orphan_attachment_contents": "select count(*) from attachment_contents c left join document_attachments a on a.id=c.attachment_id where a.id is null",
            "orphan_attachment_chunks": "select count(*) from attachment_chunks c left join attachment_contents x on x.id=c.attachment_content_id where x.id is null",
            "empty_extracted_attachment_text": "select count(*) from attachment_contents where extraction_status='extracted' and (extracted_text is null or trim(extracted_text)='')",
            "unprocessed_active_attachments": """select count(*) from document_attachments a
                left join attachment_contents c on c.attachment_id=a.id
                where a.status='active' and c.id is null""",
            "failed_attachment_extractions": "select count(*) from attachment_contents where extraction_status='failed'",
            "invalid_attachment_status": "select count(*) from attachment_contents where extraction_status not in ('extracted','ocr_required','ocr_no_text','unsupported','failed','source_unavailable','too_large')",
        }
        required = {
            name: int(session.execute(text(sql)).scalar_one()) for name, sql in required_checks.items()
        }
        for name, count in required.items():
            if count:
                failures.append(f"{name}: {count}")

        latest_runs = {
            row.dataset: dict(row._mapping)
            for row in session.execute(
                text("""
                select dataset,status,records_found,records_inserted,records_updated,
                       records_unchanged,records_deactivated
                from dataset_sync_runs r
                where id=(select max(id) from dataset_sync_runs x where x.dataset=r.dataset)
            """)
            )
        }
        for dataset, expected in EXPECTED.items():
            run = latest_runs.get(dataset)
            if not run:
                failures.append(f"{dataset}: missing sync run")
            elif run["status"] != "completed" or run["records_found"] != expected:
                failures.append(f"{dataset}: invalid latest run {run}")
            elif run["records_inserted"] or run["records_updated"] or run["records_deactivated"]:
                failures.append(f"{dataset}: latest run was not idempotent {run}")
            elif run["records_unchanged"] != expected:
                failures.append(f"{dataset}: unchanged count mismatch {run}")

        fk_violations = (
            [list(row) for row in session.execute(text("pragma foreign_key_check"))]
            if engine.dialect.name == "sqlite"
            else []
        )
        if engine.dialect.name == "postgresql":
            try:
                verify_constraints(session.connection(), settings.database_schema)
            except TransferError as error:
                failures.append(str(error))
        if fk_violations:
            failures.append(f"foreign key violations: {len(fk_violations)}")
        stats = {
            "ok": not failures,
            "counts": counts,
            "container_aliases": int(
                session.execute(text("select count(*) from container_aliases")).scalar_one()
            ),
            "container_test_mentions": int(
                session.execute(text("select count(*) from container_test_mentions")).scalar_one()
            ),
            "attachments": int(
                session.execute(
                    text("select count(*) from document_attachments where status='active'")
                ).scalar_one()
            ),
            "test_taxonomy_links": int(
                session.execute(text("select count(*) from test_taxonomy_links")).scalar_one()
            ),
            "attachment_extraction": {
                row.status: row.count
                for row in session.execute(
                    text(
                        "select extraction_status status,count(*) count from attachment_contents group by extraction_status"
                    )
                )
            },
            "faq_candidates": int(session.execute(text("select count(*) from faq_candidates")).scalar_one()),
            "handoff_requests": int(
                session.execute(text("select count(*) from handoff_requests")).scalar_one()
            ),
            "chat_feedback": int(session.execute(text("select count(*) from chat_feedback")).scalar_one()),
            "duplicates": duplicates,
            "required_or_orphan_errors": required,
            "foreign_key_violations": fk_violations,
            "failures": failures,
        }
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
