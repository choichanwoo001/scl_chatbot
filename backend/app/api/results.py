from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import AppServices, get_services
from ..schemas import (
    ResultAuthResponse,
    ResultCredentials,
    ResultDetail,
    ResultListItem,
)

router = APIRouter(prefix="/api/results")


@router.post("/authenticate", response_model=ResultAuthResponse)
def authenticate_results(
    request: ResultCredentials,
    services: AppServices = Depends(get_services),
) -> ResultAuthResponse:
    context = services.result_service.authenticate(request)
    return ResultAuthResponse(
        authenticated=True,
        expires_at=context.expires_at.isoformat(),
        provider=services.result_service.provider.name,
    )


@router.get("", response_model=list[ResultListItem])
def list_results(
    session_id: str = Query(min_length=1, max_length=80),
    services: AppServices = Depends(get_services),
) -> list[ResultListItem]:
    return services.result_service.list_results(session_id)


@router.get("/{result_id}", response_model=ResultDetail)
def get_result_detail(
    result_id: str,
    session_id: str = Query(min_length=1, max_length=80),
    services: AppServices = Depends(get_services),
) -> ResultDetail:
    try:
        return services.result_service.get_result(session_id, result_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="검사결과를 찾을 수 없습니다.") from error


@router.delete("/session/{session_id}", status_code=204)
def logout_results(
    session_id: str,
    services: AppServices = Depends(get_services),
) -> None:
    services.result_service.clear(session_id)
