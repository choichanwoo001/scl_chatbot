from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import AppServices, get_services
from ..public_search import SEARCH_TYPES
from ..schemas import PublicDataStatus, PublicSearchHit

router = APIRouter(prefix="/api")


@router.get("/public-data/status", response_model=PublicDataStatus)
async def public_data_status(services: AppServices = Depends(get_services)) -> PublicDataStatus:
    return PublicDataStatus.model_validate(services.public_search.status())


@router.get("/search", response_model=list[PublicSearchHit])
async def unified_search(
    q: str = Query(min_length=1, max_length=500),
    types: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=10, ge=1, le=50),
    services: AppServices = Depends(get_services),
) -> list[PublicSearchHit]:
    selected = None
    if types:
        selected = {item.strip() for item in types.split(",") if item.strip()}
        unknown = selected - SEARCH_TYPES
        if unknown:
            raise HTTPException(
                status_code=422, detail=f"지원하지 않는 검색 유형: {', '.join(sorted(unknown))}"
            )
    return [
        PublicSearchHit.model_validate(hit.__dict__)
        for hit in services.public_search.search(q, selected, limit)
    ]


@router.get("/public-data/{entity_type}/{entity_id}", response_model=PublicSearchHit)
async def public_data_detail(
    entity_type: str,
    entity_id: str,
    services: AppServices = Depends(get_services),
) -> PublicSearchHit:
    if entity_type not in SEARCH_TYPES:
        raise HTTPException(status_code=404, detail="지원하지 않는 공개 데이터 유형입니다.")
    hit = services.public_search.get(entity_type, entity_id)
    if hit is None:
        raise HTTPException(status_code=404, detail="공개 데이터를 찾을 수 없습니다.")
    return PublicSearchHit.model_validate(hit.__dict__)
