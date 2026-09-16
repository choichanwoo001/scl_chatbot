from __future__ import annotations

from sqlalchemy import select

from .models import (
    Container,
    ContainerAlias,
    ContainerTestMention,
    PreservativeGuide,
    TaxonomyTerm,
    Test,
    TestTaxonomyLink,
)
from .search_types import SearchHit


class CatalogSearchHandlers:
    def _search_test(self, query, normalized, limit, test_candidates):
        hits: list[SearchHit] = []
        non_test_intent = any(
            cue in normalized
            for cue in [
                "공문",
                "공지",
                "일정",
                "변경",
                "뉴스",
                "건강",
                "사회공헌",
                "자료",
                "리플릿",
                "지점",
                "센터",
                "주소",
                "전화",
                "팩스",
                "연락처",
                "메뉴",
                "페이지",
                "경로",
                "링크",
                "보존제",
                "24시간뇨",
                "차광",
                "문서",
                "첨부",
                "파일",
                "양식",
                "다운로드",
            ]
        )
        test_base = 55 if non_test_intent else 180
        candidates = (
            list(test_candidates)[: max(limit, 10)]
            if test_candidates is not None
            else self.catalog.search(query, limit=max(limit, 10))
        )
        for index, item in enumerate(candidates):
            hits.append(
                SearchHit(
                    ref=f"test:{item.variant_key or item.code}",
                    entity_type="test",
                    entity_id=item.variant_key or item.code,
                    title=item.name,
                    snippet=f"{item.specimen} · {item.method} · 소요일 {item.tat}",
                    source_url=str(item.source_url) if item.source_url else None,
                    updated_at=item.updated_at,
                    score=test_base - index,
                    metadata={
                        "code": item.code,
                        "variant_key": item.variant_key,
                        "specimen": item.specimen,
                        "container": item.container,
                    },
                )
            )
        return hits

    def _search_container(self, normalized, terms, session):
        hits: list[SearchHit] = []
        aliases = self._group_values(
            session.execute(select(ContainerAlias.container_id, ContainerAlias.alias))
        )
        mentions = self._group_values(
            session.execute(select(ContainerTestMention.container_id, ContainerTestMention.mention_text))
        )
        for item in session.scalars(select(Container).where(Container.status == "active")):
            extra = " ".join(
                [
                    item.additive or "",
                    item.major_tests_text or "",
                    *aliases.get(item.id, []),
                    *mentions.get(item.id, []),
                ]
            )
            base_score = self._score(normalized, terms, item.name, extra)
            score = base_score + self._type_boost(normalized, "container") if base_score or not terms else 0
            if score > 0:
                hits.append(
                    SearchHit(
                        ref=f"container:{item.id}",
                        entity_type="container",
                        entity_id=str(item.id),
                        title=item.name,
                        snippet=self._snippet(item.major_tests_text or item.caution_text),
                        source_url=item.source_url,
                        updated_at=self._date(item.last_seen_at),
                        score=score,
                        metadata={
                            "additive": item.additive,
                            "storage": item.storage_text,
                            "collection_volume": item.collection_volume_text,
                            "aliases": aliases.get(item.id, []),
                        },
                    )
                )
        return hits

    def _search_preservative(self, normalized, terms, session):
        hits: list[SearchHit] = []
        for item in session.scalars(select(PreservativeGuide).where(PreservativeGuide.status == "active")):
            guide = " ".join(
                filter(
                    None,
                    [
                        item.light_protection,
                        item.acetic_acid_50,
                        item.hcl_6n,
                        item.boric_acid_10g,
                        item.sodium_carbonate_5g,
                        item.no_preservative,
                    ],
                )
            )
            base_score = self._score(normalized, terms, item.test_name, guide)
            score = (
                base_score + self._type_boost(normalized, "preservative") if base_score or not terms else 0
            )
            if score > 0:
                hits.append(
                    SearchHit(
                        ref=f"preservative:{item.id}",
                        entity_type="preservative",
                        entity_id=str(item.id),
                        title=item.test_name,
                        snippet=guide or None,
                        source_url=item.source_url,
                        updated_at=self._date(item.last_seen_at),
                        score=score,
                        metadata={
                            "light_protection": item.light_protection,
                            "acetic_acid_50": item.acetic_acid_50,
                            "hcl_6n": item.hcl_6n,
                            "boric_acid_10g": item.boric_acid_10g,
                            "sodium_carbonate_5g": item.sodium_carbonate_5g,
                            "no_preservative": item.no_preservative,
                        },
                    )
                )
        return hits

    def _search_taxonomy(self, normalized, terms, session, allowed):
        hits: list[SearchHit] = []
        for item in session.scalars(select(TaxonomyTerm)):
            score = self._score(normalized, terms, item.name, item.description or "")
            if score > 0:
                if "taxonomy" in allowed:
                    hits.append(
                        SearchHit(
                            ref=f"taxonomy:{item.id}",
                            entity_type="taxonomy",
                            entity_id=str(item.id),
                            title=item.name,
                            snippet=self._snippet(item.description),
                            source_url=item.source_url,
                            updated_at=None,
                            score=score,
                            metadata={"taxonomy": item.taxonomy},
                        )
                    )
                if item.taxonomy == "disease_group" and "test" in allowed:
                    linked_tests = session.execute(
                        select(TestTaxonomyLink, Test)
                        .join(Test, Test.id == TestTaxonomyLink.test_id)
                        .where(TestTaxonomyLink.taxonomy_term_id == item.id, Test.status == "active")
                        .order_by(Test.name)
                        .limit(30)
                    )
                    for index, (link, test) in enumerate(linked_tests):
                        variants = [
                            candidate
                            for candidate in self.catalog.search(test.source_test_code, limit=5)
                            if candidate.code == test.source_test_code
                        ]
                        if not variants:
                            continue
                        candidate = variants[0]
                        hits.append(
                            SearchHit(
                                ref=f"test:{candidate.variant_key or candidate.code}",
                                entity_type="test",
                                entity_id=candidate.variant_key or candidate.code,
                                title=candidate.name,
                                snippet=f"{item.name} 큐레이션 · {candidate.specimen} · {candidate.method}",
                                source_url=str(candidate.source_url) if candidate.source_url else None,
                                updated_at=candidate.updated_at,
                                score=165 - index * 0.01,
                                metadata={
                                    "code": candidate.code,
                                    "variant_key": candidate.variant_key,
                                    "disease_group": item.name,
                                    "curation_source": link.source,
                                    "verified": link.verified,
                                },
                            )
                        )
        return hits
