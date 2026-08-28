from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "scl_catalog.db"
OUTPUT = ROOT / "frontend" / "worker" / "catalog-data.js"
VECTOR_INDEX = ROOT / "data" / "gemini_vector_index.json"


def compact_details(raw: str | None) -> dict[str, str]:
    values = json.loads(raw or "{}")
    preferred = (
        "임상적 의의",
        "채취방법 및 주의사항",
        "검사정보",
        "참고치",
        "검사방법",
    )
    result: dict[str, str] = {}
    for key in preferred:
        value = str(values.get(key) or "").strip()
        if value:
            result[key] = value[:700]
    return result


def main() -> None:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row

    aliases: dict[int, list[str]] = {}
    for row in connection.execute(
        "SELECT test_id, alias FROM test_aliases WHERE verified = 1 ORDER BY id"
    ):
        aliases.setdefault(row["test_id"], []).append(row["alias"])

    catalog = []
    query = """
        SELECT tv.id, tv.test_id, t.source_test_code AS code,
               tv.source_row_key AS variant_key, tv.display_name AS name,
               COALESCE(s.canonical_name, '정보 없음') AS specimen,
               tv.container_text AS container,
               COALESCE(m.name, '정보 없음') AS method,
               COALESCE(tv.schedule_text, '정보 없음') AS schedule,
               COALESCE(tv.tat_text, '정보 없음') AS tat,
               tv.detail_url AS source_url, tv.last_seen_at AS updated_at,
               d.fields_json
        FROM test_variants tv
        JOIN tests t ON t.id = tv.test_id
        JOIN data_sources ds ON ds.id = t.data_source_id
        LEFT JOIN specimens s ON s.id = tv.specimen_id
        LEFT JOIN methods m ON m.id = tv.method_id
        LEFT JOIN test_public_details d ON d.test_variant_id = tv.id
        WHERE ds.key = 'SCL_PUBLIC' AND t.status = 'active' AND tv.status = 'active'
        ORDER BY tv.id
    """
    for row in connection.execute(query):
        item = {
            "code": row["code"],
            "variant_key": row["variant_key"],
            "name": row["name"],
            "aliases": aliases.get(row["test_id"], [])[:6],
            "specimen": row["specimen"],
            "container": row["container"],
            "method": row["method"],
            "schedule": row["schedule"],
            "tat": row["tat"],
            "source_title": "SCL 검사항목조회",
            "source_url": row["source_url"],
            "updated_at": str(row["updated_at"] or "")[:10],
            "demo": False,
            "public_details": compact_details(row["fields_json"]),
        }
        item["search"] = " ".join(
            str(value or "")
            for value in (
                item["code"], item["name"], *item["aliases"], item["specimen"],
                item["container"], item["method"], item["schedule"], item["tat"],
                *item["public_details"].values(),
            )
        ).casefold()
        catalog.append(item)

    vectors: dict[str, str] = {}
    if VECTOR_INDEX.is_file():
        vector_payload = json.loads(VECTOR_INDEX.read_text(encoding="utf-8"))
        vectors = {
            str(item["ref"]): str(item["vector"])
            for item in vector_payload.get("items", [])
            if item.get("ref") and item.get("vector")
        }

    public_items = []
    for row in connection.execute(
        """
        SELECT 'location' AS entity_type, id, name AS title,
               COALESCE(address, '') || CASE WHEN phone IS NULL THEN '' ELSE ' · ' || phone END AS snippet,
               source_url, last_seen_at AS updated_at
        FROM service_locations WHERE status = 'active'
        UNION ALL
        SELECT 'route', id, label, path, source_url, last_seen_at
        FROM site_routes WHERE status = 'active'
        UNION ALL
        SELECT 'document', id, title, COALESCE(summary, substr(body_text, 1, 900)), source_url, updated_at
        FROM public_documents WHERE status = 'active'
        UNION ALL
        SELECT 'attachment', da.id, da.file_name,
               substr(ac.extracted_text, 1, 900), pd.source_url, ac.extracted_at
        FROM document_attachments da
        JOIN attachment_contents ac ON ac.attachment_id = da.id
        JOIN public_documents pd ON pd.id = da.document_id
        WHERE da.status = 'active' AND pd.status = 'active'
          AND ac.extraction_status = 'extracted' AND length(trim(ac.extracted_text)) > 0
        ORDER BY entity_type, id
        """
    ):
        item = {
            "ref": f"{row['entity_type']}:{row['id']}",
            "entity_type": row["entity_type"],
            "title": row["title"],
            "snippet": (row["snippet"] or "")[:900],
            "source_url": row["source_url"],
            "updated_at": str(row["updated_at"] or "")[:10],
        }
        item["search"] = f"{item['title']} {item['snippet']}".casefold()
        if item["ref"] in vectors:
            item["vector"] = vectors[item["ref"]]
        public_items.append(item)

    payload = (
        "// Generated from data/scl_catalog.db by scripts/export_sites_catalog.py.\n"
        f"export const catalog = {json.dumps(catalog, ensure_ascii=False, separators=(',', ':'))};\n"
        f"export const publicItems = {json.dumps(public_items, ensure_ascii=False, separators=(',', ':'))};\n"
    )
    OUTPUT.write_text(payload, encoding="utf-8")
    print(f"Exported {len(catalog)} test variants and {len(public_items)} public records to {OUTPUT}")


if __name__ == "__main__":
    main()
