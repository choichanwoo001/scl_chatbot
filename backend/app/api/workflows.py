from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import AppServices, get_services
from ..schemas import (
    FeedbackCreateRequest,
    FeedbackReceipt,
    HandoffCreateRequest,
    HandoffReceipt,
)

router = APIRouter(prefix="/api")


@router.post("/handoff", response_model=HandoffReceipt, status_code=201)
async def create_handoff(
    request: HandoffCreateRequest,
    services: AppServices = Depends(get_services),
) -> HandoffReceipt:
    try:
        return services.workflow_service.create_handoff(request)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/handoff/{public_id}", response_model=HandoffReceipt)
async def get_handoff(
    public_id: str,
    services: AppServices = Depends(get_services),
) -> HandoffReceipt:
    result = services.workflow_service.get_handoff(public_id)
    if result is None:
        raise HTTPException(status_code=404, detail="상담 접수 내역을 찾을 수 없습니다.")
    return result


@router.post("/feedback", response_model=FeedbackReceipt, status_code=201)
async def create_feedback(
    request: FeedbackCreateRequest,
    services: AppServices = Depends(get_services),
) -> FeedbackReceipt:
    try:
        return services.workflow_service.create_feedback(request)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
