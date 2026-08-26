"""Persistent inbound deduplication and outbound delivery state for WeCom."""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func, select

from kirara_ai.database import Base, DatabaseManager


TERMINAL_STATUSES = frozenset({"accepted", "ambiguous", "dead_letter"})
INBOUND_RETRYABLE_STATUS = "retryable"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WecomInboundReceipt(Base):
    """Durable receipt for one WeCom callback message ID."""

    __tablename__ = "wecom_inbound_receipts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(128), nullable=False, unique=True, index=True)
    source = Column(String(256), nullable=False)
    status = Column(String(20), nullable=False, default="processing", index=True)
    passive_reply = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class WecomDelivery(Base):
    """One recoverable WeCom outbound send unit."""

    __tablename__ = "wecom_outbox_deliveries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    delivery_id = Column(String(64), nullable=False, unique=True, index=True)
    recipient_key = Column(String(256), nullable=False, index=True)
    action = Column(String(64), nullable=False)
    params_json = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="queued", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    upstream_accepted = Column(Boolean, nullable=False, default=False)
    client_received = Column(Boolean, nullable=True)
    response_json = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    ambiguous_at = Column(DateTime, nullable=True)


Sender = Callable[[dict[str, Any]], Awaitable[Any] | Any]


@dataclass(frozen=True)
class WecomDeliveryResult:
    id: int
    delivery_id: str
    recipient_key: str
    status: str
    attempt_count: int
    upstream_accepted: bool
    client_received: Optional[bool]
    error_message: Optional[str]
    error: Optional[BaseException] = None


class WecomOutboxService:
    """Persist WeCom operations and quarantine unknown message outcomes."""

    def __init__(self, database: DatabaseManager, sender: Sender):
        self.database = database
        self.sender = sender
        self._lock = threading.RLock()
        self._recipient_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _snapshot(
        item: WecomDelivery,
        error: Optional[BaseException] = None,
    ) -> WecomDeliveryResult:
        return WecomDeliveryResult(
            id=int(item.id),
            delivery_id=str(item.delivery_id),
            recipient_key=str(item.recipient_key),
            status=str(item.status),
            attempt_count=int(item.attempt_count),
            upstream_accepted=bool(item.upstream_accepted),
            client_received=item.client_received,
            error_message=item.last_error,
            error=error,
        )

    def claim_inbound(self, message_id: str, source: str) -> bool:
        """Atomically claim a callback ID, allowing recovered work to retry."""
        if not message_id or len(message_id) > 128:
            raise ValueError("message_id must contain 1 to 128 characters")
        now = _utcnow()
        with self._lock, self.database.get_session() as session:
            existing = session.execute(
                select(WecomInboundReceipt).where(
                    WecomInboundReceipt.message_id == message_id
                )
            ).scalar_one_or_none()
            if existing is not None and existing.status != INBOUND_RETRYABLE_STATUS:
                return False
            if existing is not None:
                existing.source = source[:256]
                existing.status = "processing"
                existing.passive_reply = None
                existing.updated_at = now
                existing.completed_at = None
                session.commit()
                return True
            session.add(
                WecomInboundReceipt(
                    message_id=message_id,
                    source=source[:256],
                    status="processing",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            return True

    def recover_inbound(self) -> int:
        """Release inbound claims left by a previous process instance."""

        now = _utcnow()
        with self._lock, self.database.get_session() as session:
            items = list(
                session.execute(
                    select(WecomInboundReceipt).where(
                        WecomInboundReceipt.status == "processing"
                    )
                ).scalars()
            )
            for item in items:
                item.status = INBOUND_RETRYABLE_STATUS
                item.passive_reply = None
                item.updated_at = now
                item.completed_at = None
            session.commit()
            return len(items)

    def complete_inbound(self, message_id: str, passive_reply: Optional[str]) -> None:
        with self.database.get_session() as session:
            item = session.execute(
                select(WecomInboundReceipt).where(
                    WecomInboundReceipt.message_id == message_id
                )
            ).scalar_one()
            item.status = "completed"
            item.passive_reply = passive_reply
            item.completed_at = _utcnow()
            item.updated_at = item.completed_at
            session.commit()

    def retry_inbound(self, message_id: str, reason: str) -> None:
        """Make a failed or cancelled inbound callback eligible for retry."""

        with self.database.get_session() as session:
            item = session.execute(
                select(WecomInboundReceipt).where(
                    WecomInboundReceipt.message_id == message_id
                )
            ).scalar_one()
            if item.status == "completed":
                return
            item.status = INBOUND_RETRYABLE_STATUS
            item.passive_reply = None
            item.completed_at = None
            item.updated_at = _utcnow()
            session.commit()

    def enqueue(
        self,
        delivery_id: str,
        recipient_key: str,
        action: str,
        params: dict[str, Any],
    ) -> WecomDeliveryResult:
        if not delivery_id or len(delivery_id) > 64:
            raise ValueError("delivery_id must contain 1 to 64 characters")
        if not recipient_key or len(recipient_key) > 256:
            raise ValueError("recipient_key must contain 1 to 256 characters")
        now = _utcnow()
        with self._lock, self.database.get_session() as session:
            existing = session.execute(
                select(WecomDelivery).where(
                    WecomDelivery.delivery_id == delivery_id
                )
            ).scalar_one_or_none()
            if existing is not None:
                return self._snapshot(existing)
            item = WecomDelivery(
                delivery_id=delivery_id,
                recipient_key=recipient_key,
                action=action,
                params_json=json.dumps(
                    params, ensure_ascii=False, separators=(",", ":"), default=str
                ),
                status="queued",
                attempt_count=0,
                upstream_accepted=False,
                client_received=None,
                created_at=now,
                updated_at=now,
            )
            session.add(item)
            session.commit()
            return self._snapshot(item)

    def get(self, delivery_id: str) -> WecomDeliveryResult:
        with self.database.get_session() as session:
            item = session.execute(
                select(WecomDelivery).where(WecomDelivery.delivery_id == delivery_id)
            ).scalar_one()
            return self._snapshot(item)

    def recover_on_startup(self) -> int:
        now = _utcnow()
        with self.database.get_session() as session:
            items = list(
                session.execute(
                    select(WecomDelivery).where(WecomDelivery.status == "sending")
                ).scalars()
            )
            for item in items:
                item.status = "ambiguous"
                item.upstream_accepted = False
                item.client_received = None
                item.last_error = (
                    "process stopped while WeCom upstream message result was unknown"
                )
                item.ambiguous_at = now
                item.updated_at = now
            session.commit()
            return len(items)

    def pending_delivery_ids(self) -> list[str]:
        with self.database.get_session() as session:
            return list(
                session.execute(
                    select(WecomDelivery.delivery_id)
                    .where(WecomDelivery.status == "queued")
                    .order_by(WecomDelivery.created_at, WecomDelivery.id)
                ).scalars()
            )

    def status_counts(self) -> dict[str, int]:
        counts = {
            "queued": 0,
            "sending": 0,
            "accepted": 0,
            "ambiguous": 0,
            "dead_letter": 0,
        }
        with self.database.get_session() as session:
            rows = session.execute(
                select(WecomDelivery.status, func.count(WecomDelivery.id)).group_by(
                    WecomDelivery.status
                )
            ).all()
        for status, count in rows:
            if status in counts:
                counts[str(status)] = int(count)
        return counts

    async def resume_pending(self) -> list[WecomDeliveryResult]:
        return list(
            await asyncio.gather(
                *(self.deliver(item) for item in self.pending_delivery_ids())
            )
        )

    async def deliver(self, delivery_id: str) -> WecomDeliveryResult:
        target = self.get(delivery_id)
        if target.status in TERMINAL_STATUSES:
            return target
        lock = self._recipient_locks.setdefault(target.recipient_key, asyncio.Lock())
        async with lock:
            current = self.get(delivery_id)
            if current.status in TERMINAL_STATUSES:
                return current
            with self.database.get_session() as session:
                item = session.execute(
                    select(WecomDelivery).where(
                        WecomDelivery.delivery_id == delivery_id
                    )
                ).scalar_one()
                item.status = "sending"
                item.attempt_count += 1
                item.updated_at = _utcnow()
                params = json.loads(str(item.params_json))
                session.commit()
            try:
                response = self.sender(params)
                if inspect.isawaitable(response):
                    response = await response
                if response is None:
                    raise RuntimeError("WeCom API returned no result")
            except asyncio.CancelledError:
                self._mark_ambiguous(delivery_id, "delivery cancelled during WeCom send")
                raise
            except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
                return self._mark_ambiguous(delivery_id, str(exc), exc)
            except BaseException as exc:
                return self._mark_dead_letter(delivery_id, str(exc), exc)
            return self._mark_accepted(delivery_id, response)

    def _mark_accepted(self, delivery_id: str, response: Any) -> WecomDeliveryResult:
        now = _utcnow()
        with self.database.get_session() as session:
            item = session.execute(
                select(WecomDelivery).where(WecomDelivery.delivery_id == delivery_id)
            ).scalar_one()
            item.status = "accepted"
            item.upstream_accepted = True
            item.client_received = None
            item.response_json = json.dumps(
                response, ensure_ascii=False, separators=(",", ":"), default=str
            )
            item.last_error = None
            item.accepted_at = now
            item.updated_at = now
            session.commit()
            return self._snapshot(item)

    def _mark_ambiguous(
        self,
        delivery_id: str,
        error: str,
        exception: Optional[BaseException] = None,
    ) -> WecomDeliveryResult:
        now = _utcnow()
        with self.database.get_session() as session:
            item = session.execute(
                select(WecomDelivery).where(WecomDelivery.delivery_id == delivery_id)
            ).scalar_one()
            item.status = "ambiguous"
            item.upstream_accepted = False
            item.client_received = None
            item.last_error = (error or "WeCom upstream result is unknown")[:2000]
            item.ambiguous_at = now
            item.updated_at = now
            session.commit()
            return self._snapshot(item, exception)

    def _mark_dead_letter(
        self,
        delivery_id: str,
        error: str,
        exception: Optional[BaseException] = None,
    ) -> WecomDeliveryResult:
        with self.database.get_session() as session:
            item = session.execute(
                select(WecomDelivery).where(WecomDelivery.delivery_id == delivery_id)
            ).scalar_one()
            item.status = "dead_letter"
            item.last_error = (error or "WeCom delivery failed")[:2000]
            item.updated_at = _utcnow()
            session.commit()
            return self._snapshot(item, exception)
