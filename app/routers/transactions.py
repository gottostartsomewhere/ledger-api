from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_idempotency_key
from app.models.user import User
from app.schemas.errors import ErrorResponse
from app.schemas.transaction import (
    DepositRequest,
    LedgerEntryResponse,
    PaginatedHistory,
    TransferRequest,
    TransferResponse,
    WithdrawRequest,
)
from app.services.idempotency import IdempotencyService, hash_request
from app.services.ledger import LedgerService

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _serialize_transfer(transfer) -> TransferResponse:
    entries = getattr(transfer, "entries", [])
    return TransferResponse(
        id=transfer.id,
        kind=transfer.kind,
        status=transfer.status,
        amount=transfer.amount,
        currency=transfer.currency,
        description=transfer.description,
        created_at=transfer.created_at,
        entries=[LedgerEntryResponse.model_validate(e) for e in entries],
    )


async def _run_with_idempotency(
    *,
    db: AsyncSession,
    user: User,
    key: str,
    payload_dict: dict,
    runner,
) -> tuple[int, dict]:
    # Snapshot user.id eagerly. If we touch `user.id` after a rollback/commit
    # that expired ORM attributes, SQLAlchemy tries to lazy-refresh on the sync
    # path and trips MissingGreenlet under asyncpg + pool_pre_ping. Caching
    # the scalar here makes the function rollback-safe.
    user_id = user.id

    idem = IdempotencyService(db)
    request_hash = hash_request(payload_dict)

    cached = await idem.lookup(user_id, key, request_hash)
    if cached is not None:
        return cached.response_status, cached.response_body

    try:
        transfer = await runner()
        response = _serialize_transfer(transfer)
        body = jsonable_encoder(response)
        await idem.store(
            user_id=user_id,
            key=key,
            request_hash=request_hash,
            response_status=status.HTTP_201_CREATED,
            response_body=body,
            transfer_id=transfer.id,
        )
        await db.commit()
        return status.HTTP_201_CREATED, body
    except IntegrityError:
        # A concurrent request with the same (user_id, key) committed its
        # idempotency row before us. Because the idempotency insert and the
        # ledger writes share one transaction, *all* of our work rolled back
        # — no double charge. Now re-read the winner's cached response so
        # we return it instead of a generic 409.
        await db.rollback()
        cached = await idem.lookup(user_id, key, request_hash)
        if cached is None:
            raise
        return cached.response_status, cached.response_body


@router.post(
    "/deposit",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse, "description": "Idempotency key conflict"},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse, "description": "Rate limited"},
    },
)
async def deposit(
    payload: DepositRequest,
    idempotency_key: str = Depends(require_idempotency_key),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ledger = LedgerService(db)
    _, body = await _run_with_idempotency(
        db=db,
        user=user,
        key=idempotency_key,
        payload_dict={"op": "deposit", **payload.model_dump(mode="json")},
        runner=lambda: ledger.deposit(user, payload),
    )
    return body


@router.post(
    "/withdraw",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse, "description": "Idempotency key conflict"},
        422: {"model": ErrorResponse, "description": "Insufficient funds"},
        429: {"model": ErrorResponse},
    },
)
async def withdraw(
    payload: WithdrawRequest,
    idempotency_key: str = Depends(require_idempotency_key),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ledger = LedgerService(db)
    _, body = await _run_with_idempotency(
        db=db,
        user=user,
        key=idempotency_key,
        payload_dict={"op": "withdraw", **payload.model_dump(mode="json")},
        runner=lambda: ledger.withdraw(user, payload),
    )
    return body


@router.post(
    "/transfer",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse, "description": "Idempotency key conflict"},
        422: {
            "model": ErrorResponse,
            "description": "Insufficient funds, currency mismatch, or same-account transfer",
        },
        429: {"model": ErrorResponse},
    },
)
async def transfer(
    payload: TransferRequest,
    idempotency_key: str = Depends(require_idempotency_key),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ledger = LedgerService(db)
    _, body = await _run_with_idempotency(
        db=db,
        user=user,
        key=idempotency_key,
        payload_dict={"op": "transfer", **payload.model_dump(mode="json")},
        runner=lambda: ledger.transfer(user, payload),
    )
    return body


@router.get(
    "/history/{account_id}",
    response_model=PaginatedHistory,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def history(
    account_id: UUID,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedHistory:
    ledger = LedgerService(db)
    return await ledger.history(user, account_id, limit=limit, offset=offset)
