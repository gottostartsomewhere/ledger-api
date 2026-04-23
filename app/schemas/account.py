from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.account import AccountStatus


class AccountCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"currency": "USD", "name": "Main checking"}
        }
    )

    currency: str = Field(min_length=3, max_length=3, pattern="^[A-Z]{3}$", default="USD")
    name: str | None = Field(default=None, max_length=100)


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    currency: str
    balance: Decimal
    status: AccountStatus
    name: str | None
    created_at: datetime
