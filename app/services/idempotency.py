import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import IdempotencyKey
from app.services.exceptions import IdempotencyConflict


def hash_request(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyService:
    """Looks up cached responses for (user_id, key). If the cached request
    hash differs from the current request, raises IdempotencyConflict — the
    client reused a key for a different payload, which is unsafe."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def lookup(
        self, user_id: UUID, key: str, request_hash: str
    ) -> IdempotencyKey | None:
        existing = await self.db.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.key == key,
            )
        )
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise IdempotencyConflict(
                "idempotency key reused with a different request payload"
            )
        return existing

    async def store(
        self,
        user_id: UUID,
        key: str,
        request_hash: str,
        response_status: int,
        response_body: dict[str, Any],
        transfer_id: UUID | None = None,
    ) -> IdempotencyKey:
        row = IdempotencyKey(
            user_id=user_id,
            key=key,
            request_hash=request_hash,
            response_status=response_status,
            response_body=response_body,
            transfer_id=transfer_id,
        )
        self.db.add(row)
        await self.db.flush()
        return row
