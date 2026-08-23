from __future__ import annotations

import json

from app.database import SessionLocal
from app.models import PublicDocument
from app.scl_public_data import SCL_BASE_URL, SCLPublicDataSync, canonical_content_url, stable_hash


def main() -> None:
    changed: list[dict[str, str | int]] = []
    with SessionLocal.begin() as session:
        for document in session.query(PublicDocument):
            fallback = (
                f"{SCL_BASE_URL}/front/bbsList.do?bbsId={document.board_id}"
                if document.board_id
                else SCL_BASE_URL
            )
            normalized = canonical_content_url(document.source_url, fallback)
            if normalized == document.source_url:
                continue
            previous = document.source_url
            document.source_url = normalized
            document.content_hash = stable_hash(SCLPublicDataSync._document_payload(document))
            changed.append({"id": document.id, "from": previous, "to": normalized})

    print(json.dumps({"changed": len(changed), "records": changed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
