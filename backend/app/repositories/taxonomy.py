from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from ..database import SessionLocal
from ..models import TaxonomyRelation, TaxonomyTerm, Test, TestTaxonomyLink
from ..schemas import TaxonomyRelationInfo, TaxonomyTestLinkInfo


class TaxonomyRepository:
    def __init__(self, session_factory=None):
        self.session_factory = session_factory or SessionLocal

    def list_tests(self, term_id: int, limit: int) -> list[TaxonomyTestLinkInfo]:
        with self.session_factory() as session:
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

    def list_relations(self, term_id: int, limit: int) -> list[TaxonomyRelationInfo]:
        parent, child = aliased(TaxonomyTerm), aliased(TaxonomyTerm)
        with self.session_factory() as session:
            rows = session.execute(
                select(TaxonomyRelation, parent.name, child.name)
                .join(parent, parent.id == TaxonomyRelation.parent_id)
                .join(child, child.id == TaxonomyRelation.child_id)
                .where(or_(TaxonomyRelation.parent_id == term_id, TaxonomyRelation.child_id == term_id))
                .order_by(
                    TaxonomyRelation.parent_id, TaxonomyRelation.child_id, TaxonomyRelation.relation_type
                )
                .limit(limit)
            )
            return [
                TaxonomyRelationInfo(
                    parent_id=relation.parent_id,
                    parent_name=parent_name,
                    child_id=relation.child_id,
                    child_name=child_name,
                    relation_type=relation.relation_type,
                )
                for relation, parent_name, child_name in rows
            ]
