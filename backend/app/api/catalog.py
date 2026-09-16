from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ..dependencies import AppServices, get_services
from ..schemas import CatalogStatus, TaxonomyRelationInfo, TaxonomyTestLinkInfo, TestInfo

router = APIRouter(prefix="/api")


@router.get("/catalog/status", response_model=CatalogStatus)
async def catalog_status(services: AppServices = Depends(get_services)) -> CatalogStatus:
    return CatalogStatus.model_validate(services.catalog.status())


@router.get("/taxonomy/{term_id}/tests", response_model=list[TaxonomyTestLinkInfo])
async def taxonomy_tests(
    term_id: int = Path(ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    services: AppServices = Depends(get_services),
) -> list[TaxonomyTestLinkInfo]:
    return services.taxonomy_repository.list_tests(term_id, limit)


@router.get("/tests/search", response_model=list[TestInfo])
async def search_tests(
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=10, ge=1, le=50),
    services: AppServices = Depends(get_services),
) -> list[TestInfo]:
    return services.catalog.search(q, limit=limit)


@router.get("/tests/{code}", response_model=TestInfo)
async def get_test(
    code: str,
    variant_key: str | None = None,
    services: AppServices = Depends(get_services),
) -> TestInfo:
    result = services.catalog.get(code, variant_key)
    if result is not None:
        return result
    if not variant_key:
        exact = [
            item
            for item in services.catalog.search(code, limit=20)
            if item.code.casefold() == code.casefold()
        ]
        if len(exact) > 1:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "같은 검사코드에 검체별 항목이 여러 개입니다. variant_key를 지정해 주세요.",
                    "candidates": [
                        {"variant_key": item.variant_key, "name": item.name, "specimen": item.specimen}
                        for item in exact
                    ],
                },
            )
    raise HTTPException(status_code=404, detail="검사항목을 찾을 수 없습니다.")


@router.get("/taxonomy/{term_id}/relations", response_model=list[TaxonomyRelationInfo])
async def taxonomy_relations(
    term_id: int = Path(ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    services: AppServices = Depends(get_services),
) -> list[TaxonomyRelationInfo]:
    return services.taxonomy_repository.list_relations(term_id, limit)
