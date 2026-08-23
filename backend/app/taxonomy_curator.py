from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import delete, select

from .database import SessionLocal, init_database
from .models import TaxonomyRelation, TaxonomyTerm, Test, TestAlias, TestTaxonomyLink
from .normalization import normalize_search_text

CURATION_SOURCE = "curated_rule_v1"


@dataclass(frozen=True)
class DiseaseRule:
    key: str
    name: str
    patterns: tuple[str, ...]
    team_names: tuple[str, ...]


RULES = (
    DiseaseRule(
        "thyroid",
        "갑상선 질환",
        (r"\bTSH\b", r"Free\s*T[34]", r"Thyro", r"갑상선"),
        ("자동화 운영", "진단면역"),
    ),
    DiseaseRule(
        "hepatitis",
        "바이러스성 간염",
        (r"\b(?:HBV|HCV|HAV|HEV|HBs|HBe)[-\s]", r"Hepatitis", r"간염"),
        ("분자진단", "진단면역"),
    ),
    DiseaseRule(
        "diabetes",
        "당뇨병",
        (r"HbA1c", r"Hemoglobin\s*A1c", r"\bGlucose\b", r"\bInsulin\b", r"C-peptide", r"당화혈색소"),
        ("자동화 운영",),
    ),
    DiseaseRule(
        "hpv_cervical",
        "HPV·자궁경부 질환",
        (r"\bHPV\b", r"Pap\s*smear", r"자궁경부"),
        ("분자진단", "세포병리"),
    ),
    DiseaseRule(
        "allergy",
        "알레르기",
        (r"Allerg", r"Allergen", r"Specific\s*IgE", r"\bMAST\b", r"알레르"),
        ("진단면역",),
    ),
    DiseaseRule(
        "renal",
        "신장 질환",
        (r"Creatinine", r"Cystatin", r"\beGFR\b", r"\bRenal\b", r"신장"),
        ("자동화 운영",),
    ),
    DiseaseRule(
        "coagulation",
        "혈액응고 질환",
        (r"D-dimer", r"Fibrinogen", r"\baPTT\b", r"Prothrombin\s*time", r"Thrombin\s*time", r"혈액응고"),
        ("진단혈액",),
    ),
)


def sync_curated_taxonomy() -> dict[str, int]:
    init_database()
    terms_inserted = relations_inserted = links_inserted = 0
    with SessionLocal.begin() as session:
        team_terms = {
            item.name: item
            for item in session.scalars(
                select(TaxonomyTerm).where(TaxonomyTerm.taxonomy == "laboratory_team")
            )
        }
        disease_terms = {
            item.normalized_name: item
            for item in session.scalars(select(TaxonomyTerm).where(TaxonomyTerm.taxonomy == "disease_group"))
        }
        rule_terms: dict[str, TaxonomyTerm] = {}
        for rule in RULES:
            normalized = normalize_search_text(rule.name)
            term = disease_terms.get(normalized)
            if term is None:
                term = TaxonomyTerm(
                    taxonomy="disease_group",
                    name=rule.name,
                    normalized_name=normalized,
                    description=f"{CURATION_SOURCE} 규칙으로 검사명·별칭을 연결한 1차 큐레이션 분류",
                )
                session.add(term)
                session.flush()
                disease_terms[normalized] = term
                terms_inserted += 1
            rule_terms[rule.key] = term
            existing_relations = {
                (relation.child_id, relation.relation_type)
                for relation in session.scalars(
                    select(TaxonomyRelation).where(TaxonomyRelation.parent_id == term.id)
                )
            }
            for team_name in rule.team_names:
                team = team_terms.get(team_name)
                if team and (team.id, "handled_by") not in existing_relations:
                    session.add(
                        TaxonomyRelation(parent_id=term.id, child_id=team.id, relation_type="handled_by")
                    )
                    relations_inserted += 1

        aliases: dict[int, list[str]] = {}
        for test_id, alias in session.execute(select(TestAlias.test_id, TestAlias.alias)):
            aliases.setdefault(test_id, []).append(alias)
        expected: dict[tuple[int, int], str] = {}
        for test in session.scalars(select(Test).where(Test.status == "active")):
            haystack = " ".join([test.name, *aliases.get(test.id, [])])
            for rule in RULES:
                matched = next(
                    (
                        match.group(0)
                        for pattern in rule.patterns
                        if (match := re.search(pattern, haystack, re.IGNORECASE))
                    ),
                    None,
                )
                if matched:
                    expected[(test.id, rule_terms[rule.key].id)] = matched

        existing_links = {
            (link.test_id, link.taxonomy_term_id): link
            for link in session.scalars(
                select(TestTaxonomyLink).where(TestTaxonomyLink.source == CURATION_SOURCE)
            )
        }
        for key, matched_text in expected.items():
            link = existing_links.get(key)
            if link is None:
                session.add(
                    TestTaxonomyLink(
                        test_id=key[0],
                        taxonomy_term_id=key[1],
                        relation_type="associated_with",
                        matched_text=matched_text,
                        source=CURATION_SOURCE,
                        verified=False,
                    )
                )
                links_inserted += 1
            else:
                link.matched_text = matched_text
        stale_ids = [link.id for key, link in existing_links.items() if key not in expected]
        if stale_ids:
            session.execute(delete(TestTaxonomyLink).where(TestTaxonomyLink.id.in_(stale_ids)))
        return {
            "rules": len(RULES),
            "terms_inserted": terms_inserted,
            "relations_inserted": relations_inserted,
            "links_inserted": links_inserted,
            "links_unchanged": len(expected) - links_inserted,
            "links_removed": len(stale_ids),
            "total_links": len(expected),
        }
