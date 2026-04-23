from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.account import AccountCreateRequest, AccountResponse
from app.schemas.errors import ErrorResponse
from app.services.account import AccountService

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post(
    "",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse},
    },
)
async def create_account(
    payload: AccountCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    service = AccountService(db)
    account = await service.create(user, payload)
    return AccountResponse.model_validate(account)


@router.get("", response_model=list[AccountResponse])
async def list_accounts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AccountResponse]:
    service = AccountService(db)
    accounts = await service.list_for_user(user)
    return [AccountResponse.model_validate(a) for a in accounts]


@router.get(
    "/{account_id}",
    response_model=AccountResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_account(
    account_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    service = AccountService(db)
    account = await service.get_for_user(user, account_id)
    return AccountResponse.model_validate(account)
