from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class WebhookCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "url": "https://hooks.myapp.com/ledger",
                "events": ["transfer.transfer.completed", "transfer.deposit.completed"],
            }
        }
    )

    url: HttpUrl
    events: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Event types to subscribe to. Use `*` for all events.",
    )


class WebhookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    events: list[str]
    active: bool
    created_at: datetime
    secret: str | None = Field(
        default=None,
        description="Only returned on creation. Store it — used to verify "
        "signatures in the `X-Ledger-Signature` header.",
    )
