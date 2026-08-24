"""Persistent, recipient-ordered delivery queue for OneBot actions."""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from aiocqhttp.exceptions import ActionFailed, ApiNotAvailable, NetworkError
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)

from kirara_ai.database import Base, DatabaseManager


TERMINAL_STATUSES = frozenset({"accepted", "ambiguous", "dead_letter"})
PENDING_STATUSES = frozenset({"queued", "retry_wait"})


class OneBotRetryableError(RuntimeError):
    """The upstream explicitly rejected an action before completing it."""


class OneBotDelivery(Base):
    """One independently recoverable OneBot action, usually one message page."""

    __tablename__ = "onebot_outbox_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "recipient_key",
            "recipient_sequence",
            name="uq_onebot_outbox_recipient_sequence",
        ),
        Index(
            "idx_onebot_outbox_recipient_status_sequence",
            "recipient_key",
            "status",
            "recipient_sequence",
        ),
        Index("idx_onebot_outbox_status_next_attempt", "status", "next_attempt_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    delivery_id = Column(String(64), nullable=False, unique=True, index=True)
    logical_delivery_id = Column(String(64), nullable=False, index=True)
    recipient_key = Column(String(256), nullable=False, index=True)
    recipient_sequence = Column(Integer, nullable=False)
    page_index = Column(Integer, nullable=False, default=0)
    page_count = Column(Integer, nullable=False, default=1)
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
    next_attempt_at = Column(DateTime, nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    ambiguous_at = Column(DateTime, nullable=True)


@dataclass(frozen=True)
class OneBotDeliveryResult:
    id: int
    delivery_id: str
    logical_delivery_id: str
    recipient_key: str
    recipient_sequence: int
    page_index: int
    page_count: int
    status: str
    attempt_count: int
    upstream_accepted: bool
    client_received: Optional[bool]
    error_message: Optional[str]
    error: Optional[BaseException] = None


@dataclass
class _RecipientLockState:
    lock: asyncio.Lock
    users: int = 0


Sender = Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OneBotOutboxService:
    """Persist actions and deliver them in order without retrying unknown results."""

    def __init__(
        self,
        database: DatabaseManager,
        sender: Sender,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        self.database = database
        self.sender = sender
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self._enqueue_lock = threading.RLock()
        self._recipient_locks: dict[str, _RecipientLockState] = {}

    @staticmethod
    def _snapshot(
        delivery: OneBotDelivery,
        error: Optional[BaseException] = None,
    ) -> OneBotDeliveryResult:
        return OneBotDeliveryResult(
            id=int(delivery.id),
            delivery_id=str(delivery.delivery_id),
            logical_delivery_id=str(delivery.logical_delivery_id),
            recipient_key=str(delivery.recipient_key),
            recipient_sequence=int(delivery.recipient_sequence),
            page_index=int(delivery.page_index),
            page_count=int(delivery.page_count),
            status=str(delivery.status),
            attempt_count=int(delivery.attempt_count),
            upstream_accepted=bool(delivery.upstream_accepted),
            client_received=delivery.client_received,
            error_message=delivery.last_error,
            error=error,
        )

    def enqueue(
        self,
        delivery_id: str,
        recipient_key: str,
        action: str,
        params: dict[str, Any],
        *,
        logical_delivery_id: Optional[str] = None,
        page_index: int = 0,
        page_count: int = 1,
    ) -> OneBotDeliveryResult:
        if not delivery_id or len(delivery_id) > 64:
            raise ValueError("delivery_id must contain 1 to 64 characters")
        if not recipient_key or len(recipient_key) > 256:
            raise ValueError("recipient_key must contain 1 to 256 characters")
        if page_count < 1 or page_index < 0 or page_index >= page_count:
            raise ValueError("page index must be within page count")
        encoded_params = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
        logical_id = logical_delivery_id or delivery_id
        now = _utcnow()

        with self._enqueue_lock, self.database.get_session() as session:
            existing = session.execute(
                select(OneBotDelivery).where(
                    OneBotDelivery.delivery_id == delivery_id
                )
            ).scalar_one_or_none()
            if existing is not None:
                return self._snapshot(existing)

            sequence = session.execute(
                select(func.max(OneBotDelivery.recipient_sequence)).where(
                    OneBotDelivery.recipient_key == recipient_key
                )
            ).scalar_one_or_none()
            delivery = OneBotDelivery(
                delivery_id=delivery_id,
                logical_delivery_id=logical_id,
                recipient_key=recipient_key,
                recipient_sequence=int(sequence or 0) + 1,
                page_index=page_index,
                page_count=page_count,
                action=action,
                params_json=encoded_params,
                status="queued",
                attempt_count=0,
                upstream_accepted=False,
                client_received=None,
                created_at=now,
                updated_at=now,
            )
            session.add(delivery)
            session.commit()
            return self._snapshot(delivery)

    def get(self, delivery_id: str) -> OneBotDeliveryResult:
        with self.database.get_session() as session:
            delivery = session.execute(
                select(OneBotDelivery).where(
                    OneBotDelivery.delivery_id == delivery_id
                )
            ).scalar_one()
            return self._snapshot(delivery)

    def recover_on_startup(self) -> int:
        """Quarantine actions that may have reached the upstream before shutdown."""
        now = _utcnow()
        with self.database.get_session() as session:
            deliveries = list(
                session.execute(
                    select(OneBotDelivery).where(OneBotDelivery.status == "sending")
                ).scalars()
            )
            for delivery in deliveries:
                delivery.status = "ambiguous"
                delivery.upstream_accepted = False
                delivery.client_received = None
                delivery.last_error = "process stopped while upstream result was unknown"
                delivery.ambiguous_at = now
                delivery.updated_at = now
            session.commit()
            return len(deliveries)

    def pending_delivery_ids(self) -> list[str]:
        with self.database.get_session() as session:
            return list(
                session.execute(
                    select(OneBotDelivery.delivery_id)
                    .where(OneBotDelivery.status.in_(PENDING_STATUSES))
                    .order_by(
                        OneBotDelivery.recipient_key,
                        OneBotDelivery.recipient_sequence,
                    )
                ).scalars()
            )

    def status_counts(self) -> dict[str, int]:
        counts = {
            "queued": 0,
            "sending": 0,
            "accepted": 0,
            "retry_wait": 0,
            "ambiguous": 0,
            "dead_letter": 0,
        }
        with self.database.get_session() as session:
            rows = session.execute(
                select(OneBotDelivery.status, func.count(OneBotDelivery.id)).group_by(
                    OneBotDelivery.status
                )
            ).all()
        for status, count in rows:
            if status in counts:
                counts[str(status)] = int(count)
        return counts

    async def resume_pending(self) -> list[OneBotDeliveryResult]:
        ids = self.pending_delivery_ids()
        if not ids:
            return []
        return list(await asyncio.gather(*(self.deliver(item) for item in ids)))

    async def deliver(self, delivery_id: str) -> OneBotDeliveryResult:
        target = self.get(delivery_id)
        if target.status in TERMINAL_STATUSES:
            return target
        state = self._recipient_locks.get(target.recipient_key)
        if state is None:
            state = self._recipient_locks[target.recipient_key] = _RecipientLockState(
                lock=asyncio.Lock()
            )
        state.users += 1
        try:
            async with state.lock:
                return await self._deliver_recipient_through(target)
        finally:
            state.users -= 1
            if state.users == 0 and not state.lock.locked():
                self._recipient_locks.pop(target.recipient_key, None)

    async def _deliver_recipient_through(
        self, target: OneBotDeliveryResult
    ) -> OneBotDeliveryResult:
        while True:
            target_now = self.get(target.delivery_id)
            if target_now.status in TERMINAL_STATUSES:
                return target_now

            with self.database.get_session() as session:
                blocker = session.execute(
                    select(OneBotDelivery)
                    .where(
                        OneBotDelivery.recipient_key == target.recipient_key,
                        OneBotDelivery.recipient_sequence < target.recipient_sequence,
                        OneBotDelivery.status != "accepted",
                    )
                    .order_by(OneBotDelivery.recipient_sequence)
                    .limit(1)
                ).scalar_one_or_none()
                if blocker is not None and blocker.status in {
                    "ambiguous",
                    "dead_letter",
                }:
                    return target_now

                next_delivery = session.execute(
                    select(OneBotDelivery)
                    .where(
                        OneBotDelivery.recipient_key == target.recipient_key,
                        OneBotDelivery.recipient_sequence
                        <= target.recipient_sequence,
                        OneBotDelivery.status.in_(PENDING_STATUSES),
                    )
                    .order_by(OneBotDelivery.recipient_sequence)
                    .limit(1)
                ).scalar_one_or_none()
                next_id = (
                    str(next_delivery.delivery_id)
                    if next_delivery is not None
                    else None
                )

            if next_id is None:
                return self.get(target.delivery_id)
            result = await self._deliver_one(next_id)
            if result.status != "accepted":
                return result

    async def _deliver_one(self, delivery_id: str) -> OneBotDeliveryResult:
        while True:
            delay = 0.0
            with self.database.get_session() as session:
                delivery = session.execute(
                    select(OneBotDelivery).where(
                        OneBotDelivery.delivery_id == delivery_id
                    )
                ).scalar_one()
                if delivery.status in TERMINAL_STATUSES:
                    return self._snapshot(delivery)
                if delivery.status == "retry_wait" and delivery.next_attempt_at:
                    delay = max(
                        0.0, (delivery.next_attempt_at - _utcnow()).total_seconds()
                    )
                action = str(delivery.action)
                params = json.loads(str(delivery.params_json))

            if delay:
                await asyncio.sleep(delay)

            with self.database.get_session() as session:
                delivery = session.execute(
                    select(OneBotDelivery).where(
                        OneBotDelivery.delivery_id == delivery_id
                    )
                ).scalar_one()
                if delivery.status in TERMINAL_STATUSES:
                    return self._snapshot(delivery)
                delivery.status = "sending"
                delivery.attempt_count += 1
                delivery.next_attempt_at = None
                delivery.updated_at = _utcnow()
                session.commit()
                attempt_count = int(delivery.attempt_count)

            try:
                response = self.sender(action, **params)
                if inspect.isawaitable(response):
                    response = await response
            except asyncio.CancelledError as exc:
                self._mark_ambiguous(delivery_id, "delivery cancelled during action")
                raise
            except (asyncio.TimeoutError, NetworkError, ConnectionError, OSError) as exc:
                return self._mark_ambiguous(delivery_id, str(exc), exc)
            except (OneBotRetryableError, ApiNotAvailable) as exc:
                if attempt_count >= self.max_attempts:
                    return self._mark_dead_letter(delivery_id, str(exc), exc)
                self._mark_retry_wait(delivery_id, str(exc), attempt_count)
                continue
            except ActionFailed as exc:
                return self._mark_dead_letter(delivery_id, str(exc), exc)
            except BaseException as exc:
                return self._mark_dead_letter(delivery_id, str(exc), exc)
            return self._mark_accepted(delivery_id, response)

    def _mark_accepted(
        self, delivery_id: str, response: Any
    ) -> OneBotDeliveryResult:
        now = _utcnow()
        with self.database.get_session() as session:
            delivery = session.execute(
                select(OneBotDelivery).where(
                    OneBotDelivery.delivery_id == delivery_id
                )
            ).scalar_one()
            delivery.status = "accepted"
            delivery.upstream_accepted = True
            delivery.client_received = None
            delivery.response_json = json.dumps(
                response, ensure_ascii=False, separators=(",", ":"), default=str
            )
            delivery.last_error = None
            delivery.accepted_at = now
            delivery.updated_at = now
            session.commit()
            return self._snapshot(delivery)

    def _mark_retry_wait(
        self, delivery_id: str, error: str, attempt_count: int
    ) -> None:
        delay = self.retry_delay_seconds * (2 ** max(0, attempt_count - 1))
        now = _utcnow()
        with self.database.get_session() as session:
            delivery = session.execute(
                select(OneBotDelivery).where(
                    OneBotDelivery.delivery_id == delivery_id
                )
            ).scalar_one()
            delivery.status = "retry_wait"
            delivery.last_error = error[:2000]
            delivery.next_attempt_at = now + timedelta(seconds=delay)
            delivery.updated_at = now
            session.commit()

    def _mark_ambiguous(
        self,
        delivery_id: str,
        error: str,
        exception: Optional[BaseException] = None,
    ) -> OneBotDeliveryResult:
        now = _utcnow()
        with self.database.get_session() as session:
            delivery = session.execute(
                select(OneBotDelivery).where(
                    OneBotDelivery.delivery_id == delivery_id
                )
            ).scalar_one()
            delivery.status = "ambiguous"
            delivery.upstream_accepted = False
            delivery.client_received = None
            delivery.last_error = (error or "upstream result is unknown")[:2000]
            delivery.ambiguous_at = now
            delivery.updated_at = now
            session.commit()
            return self._snapshot(delivery, exception)

    def _mark_dead_letter(
        self,
        delivery_id: str,
        error: str,
        exception: Optional[BaseException] = None,
    ) -> OneBotDeliveryResult:
        with self.database.get_session() as session:
            delivery = session.execute(
                select(OneBotDelivery).where(
                    OneBotDelivery.delivery_id == delivery_id
                )
            ).scalar_one()
            delivery.status = "dead_letter"
            delivery.upstream_accepted = False
            delivery.client_received = None
            delivery.last_error = (error or "delivery failed")[:2000]
            delivery.updated_at = _utcnow()
            session.commit()
            return self._snapshot(delivery, exception)
