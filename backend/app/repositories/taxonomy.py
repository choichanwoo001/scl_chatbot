from __future__ import annotations

from sqlalchemy import select

from ..database import SessionLocal
from ..models import Test, TestTaxonomyLink
from ..schemas import TaxonomyTestLinkInfo


class TaxonomyRepository:
    def list_tests(self, term_id: int, limit: int) -> list[TaxonomyTestLinkInfo]:
        with SessionLocal() as session:
            rows = session.execute(
                select(TestTaxonomyLink, Test)
                .join(Test, Test.id == TestTaxonomyLink.test_id)
                .where(TestTaxonomyLink.taxonomy_term_id == term_id, Test.status == "active")
                .order_by(Test.name)
                .limit(limit)
            )
            return [
                TaxonomyTestLinkInfo(
                    test_id=test.id,
                    code=test.source_test_code,
                    name=test.name,
                    relation_type=link.relation_type,
                    matched_text=link.matched_text,
                    source=link.source,
                    verified=link.verified,
                )
                for link, test in rows
            ]
