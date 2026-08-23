from app.database import SessionLocal
from app.main import app
from app.models import TaxonomyRelation, TaxonomyTerm
from app.models import TestTaxonomyLink as TaxonomyLinkModel
from app.taxonomy_curator import RULES, sync_curated_taxonomy
from fastapi.testclient import TestClient
from sqlalchemy import func, select


def test_curated_taxonomy_is_idempotent_and_links_demo_tests() -> None:
    first = sync_curated_taxonomy()
    second = sync_curated_taxonomy()
    assert first["rules"] == len(RULES)
    assert second["terms_inserted"] == 0
    assert second["relations_inserted"] == 0
    assert second["links_inserted"] == 0
    assert second["links_removed"] == 0
    with SessionLocal() as session:
        disease_terms = session.scalar(
            select(func.count()).select_from(TaxonomyTerm).where(TaxonomyTerm.taxonomy == "disease_group")
        )
        links = session.scalar(select(func.count()).select_from(TaxonomyLinkModel))
        assert disease_terms == len(RULES)
        assert links and links > 0


def test_disease_groups_have_team_relations_when_teams_exist() -> None:
    sync_curated_taxonomy()
    with SessionLocal() as session:
        team_count = session.scalar(
            select(func.count()).select_from(TaxonomyTerm).where(TaxonomyTerm.taxonomy == "laboratory_team")
        )
        relation_count = session.scalar(select(func.count()).select_from(TaxonomyRelation))
        if team_count:
            assert relation_count and relation_count > 0


def test_taxonomy_test_links_are_exposed_by_api() -> None:
    sync_curated_taxonomy()
    with SessionLocal() as session:
        term_id = session.scalar(
            select(TaxonomyLinkModel.taxonomy_term_id).order_by(TaxonomyLinkModel.id).limit(1)
        )
    response = TestClient(app).get(f"/api/taxonomy/{term_id}/tests")
    assert response.status_code == 200
    assert response.json()
    assert response.json()[0]["source"] == "curated_rule_v1"
    assert response.json()[0]["verified"] is False
