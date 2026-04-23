"""Outbox write + sweeper.

Writing: `record_event()` inserts a row in the caller's DB session — it must
be called before the caller's commit so the event is atomically tied to the
business change.

Sweeping: `run_sweeper()` is a long-lived coroutine started by the app
lifespan. It uses `FOR UPDATE SKIP LOCKED` so multiple replicas can sweep the
same table without colliding."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.outbox import OutboxEvent, OutboxStatus

logger = logging.getLogger(__name__)

EventHandler = Callable[[OutboxEvent], Awaitable[None]]


async def record_event(
    db: AsyncSession,
    event_type: str,
    payload: dict[str, Any],
    aggregate_id: UUID | None = None,
) -> OutboxEvent:
    event = OutboxEvent(
        event_type=event_type,
        aggregate_id=aggregate_id,
        payload=json.loads(json.dumps(payload, default=str)),
    )
    db.add(event)
    await db.flush()
    return event


class OutboxSweeper:
    def __init__(
        self,
        handler: EventHandler,
        *,
        batch_size: int = 25,
        interval_seconds: float = 2.0,
        max_attempts: int = 8,
    ) -> None:
        self.handler = handler
        self.batch_size = batch_size
        self.interval_seconds = interval_seconds
        self.max_attempts = max_attempts
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="outbox-sweeper")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = await self._drain_once()
            except Exception:
                logger.exception("outbox sweeper iteration failed")
                processed = 0
            if processed == 0:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass

    async def _drain_once(self) -> int:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(OutboxEvent)
                .where(OutboxEvent.status == OutboxStatus.PENDING)
                .where(OutboxEvent.attempts < self.max_attempts)
                .order_by(OutboxEvent.created_at)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            events = list((await session.scalars(stmt)).all())
            if not events:
                await session.commit()
                return 0

            for event in events:
                event.attempts += 1
                event.last_attempt_at = datetime.now(timezone.utc)
                try:
                    await self.handler(event)
                    event.status = OutboxStatus.SENT
                    event.last_error = None
                except Exception as exc:
                    event.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                    if event.attempts >= self.max_attempts:
                        event.status = OutboxStatus.FAILED
                    logger.warning(
                        "outbox event %s failed (attempt %s): %s",
                        event.id,
                        event.attempts,
                        exc,
                    )
            await session.commit()
            return len(events)


async def default_handler(event: OutboxEvent) -> None:
    """Placeholder handler — replace with webhook dispatch in prod. Also acts
    as an always-succeeds sink so the sweeper drains the outbox even when no
    real consumer is wired."""
    logger.info(
        "outbox event dispatched type=%s id=%s aggregate=%s",
        event.event_type,
        event.id,
        event.aggregate_id,
    )
