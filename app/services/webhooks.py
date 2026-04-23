"""Webhook delivery. Dispatches outbox events to registered endpoints,
HMAC-signing the body with each endpoint's secret so consumers can verify
authenticity. Called by the outbox sweeper — non-2xx responses raise, and
the sweeper retries with exponential backoff up to `max_attempts`."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.metrics import webhook_delivery_total
from app.models.outbox import OutboxEvent
from app.models.webhook import WebhookEndpoint

logger = logging.getLogger(__name__)


def generate_secret() -> str:
    return secrets.token_urlsafe(48)


def sign(secret: str, timestamp: int, body: str) -> str:
    msg = f"{timestamp}.{body}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return digest


async def _endpoints_for_event(db: AsyncSession, event_type: str) -> list[WebhookEndpoint]:
    stmt = select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True))
    rows = list((await db.scalars(stmt)).all())
    return [ep for ep in rows if event_type in ep.events or "*" in ep.events]


async def deliver_event(db: AsyncSession, event: OutboxEvent) -> None:
    endpoints = await _endpoints_for_event(db, event.event_type)
    if not endpoints:
        return  # nobody cares — still counts as delivered

    settings = get_settings()
    body = json.dumps(
        {
            "event_id": str(event.id),
            "type": event.event_type,
            "aggregate_id": str(event.aggregate_id) if event.aggregate_id else None,
            "data": event.payload,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    ts = int(time.time())
    failures: list[str] = []
    async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
        for ep in endpoints:
            sig = sign(ep.secret, ts, body)
            try:
                resp = await client.post(
                    ep.url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Ledger-Event": event.event_type,
                        "X-Ledger-Delivery": str(event.id),
                        "X-Ledger-Timestamp": str(ts),
                        "X-Ledger-Signature": f"t={ts},v1={sig}",
                    },
                )
                if resp.status_code >= 300:
                    webhook_delivery_total.labels(result="error").inc()
                    failures.append(f"{ep.url}: {resp.status_code}")
                else:
                    webhook_delivery_total.labels(result="ok").inc()
            except httpx.HTTPError as exc:
                webhook_delivery_total.labels(result="error").inc()
                failures.append(f"{ep.url}: {exc}")

    if failures:
        raise RuntimeError("; ".join(failures))


async def outbox_handler(event: OutboxEvent) -> None:
    """Sweeper-facing handler. Opens its own session so it doesn't share the
    sweeper's locking transaction with the query-heavy endpoint lookup."""
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await deliver_event(session, event)


class WebhookService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self, user_id: UUID, url: str, events: list[str]
    ) -> WebhookEndpoint:
        ep = WebhookEndpoint(
            user_id=user_id,
            url=url,
            secret=generate_secret(),
            events=events,
            active=True,
        )
        self.db.add(ep)
        await self.db.commit()
        await self.db.refresh(ep)
        return ep

    async def list_for_user(self, user_id: UUID) -> list[WebhookEndpoint]:
        result = await self.db.scalars(
            select(WebhookEndpoint).where(WebhookEndpoint.user_id == user_id)
        )
        return list(result.all())

    async def delete(self, user_id: UUID, endpoint_id: UUID) -> bool:
        ep = await self.db.get(WebhookEndpoint, endpoint_id)
        if ep is None or ep.user_id != user_id:
            return False
        await self.db.delete(ep)
        await self.db.commit()
        return True
