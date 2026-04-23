from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies_admin import require_admin
from app.models.account import Account, AccountStatus, AccountType
from app.models.user import User
from app.schemas.account import AccountResponse
from app.schemas.errors import ErrorResponse
from app.schemas.transaction import LedgerEntryResponse, TransferResponse
from app.services.fx import FxService
from app.services.ledger import LedgerService

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse, "description": "Not an admin"},
    },
)


class SetAccountStatusRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"status": "FROZEN"}})
    status: AccountStatus


class FxRateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"from_currency": "USD", "to_currency": "EUR", "rate": "0.92"}
        }
    )
    from_currency: str = Field(min_length=3, max_length=3)
    to_currency: str = Field(min_length=3, max_length=3)
    rate: Decimal = Field(gt=Decimal("0"))


class ReverseTransferRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.get("/system-accounts", response_model=list[AccountResponse])
async def list_system_accounts(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AccountResponse]:
    accounts = (
        await db.scalars(
            select(Account).where(Account.account_type == AccountType.SYSTEM)
        )
    ).all()
    return [AccountResponse.model_validate(a) for a in accounts]


@router.post(
    "/accounts/{account_id}/status",
    response_model=AccountResponse,
)
async def set_account_status(
    account_id: UUID,
    payload: SetAccountStatusRequest,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    account.status = payload.status
    await db.commit()
    await db.refresh(account)
    return AccountResponse.model_validate(account)


@router.post(
    "/fx-rates",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}},
)
async def upsert_fx_rate(
    payload: FxRateRequest,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    service = FxService(db)
    rate = await service.upsert_rate(payload.from_currency, payload.to_currency, payload.rate)
    await db.commit()
    return {
        "from_currency": rate.from_currency,
        "to_currency": rate.to_currency,
        "rate": str(rate.rate),
    }


@router.post(
    "/transfers/{transfer_id}/reverse",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorResponse}},
)
async def reverse_transfer(
    transfer_id: UUID,
    payload: ReverseTransferRequest = Body(...),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TransferResponse:
    ledger = LedgerService(db)
    reversal = await ledger.reverse(transfer_id, payload.reason)
    await db.commit()
    entries = getattr(reversal, "entries", [])
    return TransferResponse(
        id=reversal.id,
        kind=reversal.kind,
        status=reversal.status,
        amount=reversal.amount,
        currency=reversal.currency,
        description=reversal.description,
        created_at=reversal.created_at,
        entries=[LedgerEntryResponse.model_validate(e) for e in entries],
    )


@router.get("/invariant-check")
async def invariant_check(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=1000, ge=1, le=100_000),
) -> dict:
    """Scans the most recent `limit` transfers and verifies every transfer's
    per-currency debits equal its per-currency credits. Returns any offenders
    (there should be none, ever)."""
    from sqlalchemy import text

    sql = text(
        """
        WITH recent AS (
            SELECT id FROM transfers ORDER BY created_at DESC LIMIT :lim
        )
        SELECT le.transfer_id::text, le.currency,
               SUM(CASE WHEN le.entry_type='DEBIT'  THEN le.amount ELSE 0 END) AS debits,
               SUM(CASE WHEN le.entry_type='CREDIT' THEN le.amount ELSE 0 END) AS credits
        FROM ledger_entries le
        JOIN recent r ON r.id = le.transfer_id
        GROUP BY le.transfer_id, le.currency
        HAVING SUM(CASE WHEN le.entry_type='DEBIT'  THEN le.amount ELSE 0 END)
            <> SUM(CASE WHEN le.entry_type='CREDIT' THEN le.amount ELSE 0 END);
        """
    )
    rows = (await db.execute(sql, {"lim": limit})).all()
    return {
        "checked_transfers_up_to": limit,
        "offenders": [
            {
                "transfer_id": r[0],
                "currency": r[1],
                "debits": str(r[2]),
                "credits": str(r[3]),
            }
            for r in rows
        ],
    }
