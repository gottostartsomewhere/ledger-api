from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.errors import ErrorResponse
from app.schemas.webhook import WebhookCreateRequest, WebhookResponse
from app.services.webhooks import WebhookService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "",
    response_model=WebhookResponse,
    status_code=status.HTTP_201_CREATED,
    responses={401: {"model": ErrorResponse}},
)
async def create_webhook(
    payload: WebhookCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WebhookResponse:
    service = WebhookService(db)
    ep = await service.create(user.id, str(payload.url), payload.events)
    resp = WebhookResponse.model_validate(ep)
    resp.secret = ep.secret
    return resp


@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WebhookResponse]:
    service = WebhookService(db)
    return [WebhookResponse.model_validate(ep) for ep in await service.list_for_user(user.id)]


@router.delete(
    "/{endpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def delete_webhook(
    endpoint_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    service = WebhookService(db)
    ok = await service.delete(user.id, endpoint_id)
    if not ok:
        raise HTTPException(status_code=404, detail="webhook endpoint not found")
