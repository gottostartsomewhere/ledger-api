from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.transaction import EntryType, TransferKind, TransferStatus


class DepositRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": "6f1f0a60-7e5d-4a45-8c59-1a6b26b8a2f3",
                "amount": "100.00",
                "description": "Payday deposit",
            }
        }
    )

    account_id: UUID
    amount: Decimal = Field(gt=Decimal("0"), decimal_places=4)
    description: str | None = Field(default=None, max_length=500)


class WithdrawRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": "6f1f0a60-7e5d-4a45-8c59-1a6b26b8a2f3",
                "amount": "25.50",
                "description": "ATM withdrawal",
            }
        }
    )

    account_id: UUID
    amount: Decimal = Field(gt=Decimal("0"), decimal_places=4)
    description: str | None = Field(default=None, max_length=500)


class TransferRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "from_account_id": "6f1f0a60-7e5d-4a45-8c59-1a6b26b8a2f3",
                "to_account_id": "ad2a0f54-8c2f-4b53-9f7c-0b4f6a3d91c0",
                "amount": "42.00",
                "description": "Splitting dinner",
            }
        }
    )

    from_account_id: UUID
    to_account_id: UUID
    amount: Decimal = Field(gt=Decimal("0"), decimal_places=4)
    description: str | None = Field(default=None, max_length=500)


class LedgerEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    transfer_id: UUID
    account_id: UUID
    entry_type: EntryType
    amount: Decimal
    currency: str
    created_at: datetime


class TransferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: TransferKind
    status: TransferStatus
    amount: Decimal
    currency: str
    description: str | None
    created_at: datetime
    entries: list[LedgerEntryResponse] = Field(default_factory=list)


class TransactionHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entry_id: UUID
    transfer_id: UUID
    account_id: UUID
    entry_type: EntryType
    amount: Decimal
    currency: str
    kind: TransferKind
    description: str | None
    created_at: datetime


class PaginatedHistory(BaseModel):
    items: list[TransactionHistoryItem]
    limit: int
    offset: int
    total: int
