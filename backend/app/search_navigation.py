from __future__ import annotations

from sqlalchemy import select

from .models import (
    ServiceLocation,
    SiteRoute,
)
from .search_types import SearchHit


class NavigationSearchHandlers:
    def _search_location(self, normalized, terms, session):
        hits: list[SearchHit] = []
        for item in session.scalars(select(ServiceLocation).where(ServiceLocation.status == "active")):
            extra = " ".join(filter(None, [item.region, item.address, item.phone, item.fax]))
            base_score = self._score(normalized, terms, item.name, extra)
            score = base_score + self._type_boost(normalized, "location") if base_score or not terms else 0
            if score > 0:
                hits.append(
                    SearchHit(
                        ref=f"location:{item.id}",
                        entity_type="location",
                        entity_id=str(item.id),
                        title=item.name,
                        snippet=" · ".join(filter(None, [item.address, item.phone])),
                        source_url=item.source_url,
                        updated_at=self._date(item.last_seen_at),
                        score=score,
                        metadata={
                            "location_type": item.location_type,
                            "region": item.region,
                            "address": item.address,
                            "phone": item.phone,
                            "fax": item.fax,
                        },
                    )
                )
        return hits

    def _search_route(self, normalized, terms, session):
        hits: list[SearchHit] = []
        for item in session.scalars(select(SiteRoute).where(SiteRoute.status == "active")):
            base_score = self._score(normalized, terms, item.label, item.path)
            score = base_score + self._type_boost(normalized, "route") if base_score or not terms else 0
            if score > 0:
                absolute = (
                    item.path if item.path.startswith("http") else f"https://www.scllab.co.kr{item.path}"
                )
                hits.append(
                    SearchHit(
                        ref=f"route:{item.id}",
                        entity_type="route",
                        entity_id=str(item.id),
                        title=item.label,
                        snippet=item.path,
                        source_url=absolute,
                        updated_at=self._date(item.last_seen_at),
                        score=score,
                        metadata={
                            "path": item.path,
                            "parent_path": item.parent_path,
                            "depth": item.depth,
                        },
                    )
                )
        return hits
