"""Durable Telegram inbound receipts and ordered outbound delivery."""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

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
from kirara_ai.im.outbox_backoff import retry_backoff_seconds


TERMINAL_STATUSES = frozenset({"accepted", "ambiguous", "dead_letter"})
PENDING_STATUSES = frozenset({"queued", "retry_wait"})
INBOUND_RETRYABLE_STATUS = "retryable"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TelegramInboundReceipt(Base):
    """One Telegram update claimed by one configured bot instance."""

    __tablename__ = "telegram_inbound_receipts"
    __table_args__ = (
        UniqueConstraint(
            "adapter_instance",
            "update_id",
            name="uq_telegram_inbound_adapter_update",
        ),
        Index(
            "idx_telegram_inbound_status",
            "adapter_instance",
            "status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    adapter_instance = Column(String(128), nullable=False)
    update_id = Column(String(64), nullable=False)
    chat_key = Column(String(256), nullable=False)
    # Keep a replayable Update only while inbound processing is unfinished.
    payload_json = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="processing", index=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class TelegramDelivery(Base):
    """One Telegram text/media page that can be resumed independently."""

    __tablename__ = "telegram_outbox_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "recipient_key",
            "recipient_sequence",
            name="uq_telegram_outbox_recipient_sequence",
        ),
        Index(
            "idx_telegram_outbox_recipient_status_sequence",
            "recipient_key",
            "status",
            "recipient_sequence",
        ),
        Index("idx_telegram_outbox_status", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    delivery_id = Column(String(64), nullable=False, unique=True, index=True)
    logical_delivery_id = Column(String(64), nullable=False, index=True)
    adapter_instance = Column(String(128), nullable=False, index=True)
    recipient_key = Column(String(256), nullable=False, index=True)
    recipient_sequence = Column(Integer, nullable=False)
    page_index = Column(Integer, nullable=False)
    page_count = Column(Integer, nullable=False)
    action = Column(String(32), nullable=False)
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


class TelegramRetryableError(RuntimeError):
    """The upstream explicitly rejected a request before accepting it."""


@dataclass(frozen=True)
class TelegramDeliveryResult:
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


Sender = Callable[[dict[str, Any]], Awaitable[Any] | Any]


class TelegramOutboxService:
    """Persist Telegram sends and quarantine an unknown upstream result."""

    def __init__(
        self,
        database: DatabaseManager,
        sender: Sender,
        *,
        adapter_instance: str,
        max_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
    ):
        if not adapter_instance:
            raise ValueError("adapter_instance is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.database = database
        self.sender = sender
        self.adapter_instance = adapter_instance
        self.max_attempts = max_attempts
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self._lock = threading.RLock()
        self._recipient_locks: dict[str, _RecipientLockState] = {}

    @staticmethod
    def _snapshot(
        item: TelegramDelivery,
        error: Optional[BaseException] = None,
    ) -> TelegramDeliveryResult:
        return TelegramDeliveryResult(
            id=int(item.id),
            delivery_id=str(item.delivery_id),
            logical_delivery_id=str(item.logical_delivery_id),
            recipient_key=str(item.recipient_key),
            recipient_sequence=int(item.recipient_sequence),
            page_index=int(item.page_index),
            page_count=int(item.page_count),
            status=str(item.status),
            attempt_count=int(item.attempt_count),
            upstream_accepted=bool(item.upstream_accepted),
            client_received=item.client_received,
            error_message=item.last_error,
            error=error,
        )

    def claim_inbound(
        self,
        update_id: int | str,
        chat_key: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        update_value = str(update_id)
        if not update_value or len(update_value) > 64:
            raise ValueError("update_id is invalid")
        now = _utcnow()
        with self._lock, self.database.get_session() as session:
            query = select(TelegramInboundReceipt).where(
                TelegramInboundReceipt.adapter_instance == self.adapter_instance,
                TelegramInboundReceipt.update_id == update_value,
            )
            existing = session.execute(query).scalar_one_or_none()
            if existing is not None and existing.status != INBOUND_RETRYABLE_STATUS:
                return False
            if existing is None:
                session.add(
                    TelegramInboundReceipt(
                        adapter_instance=self.adapter_instance,
                        update_id=update_value,
                        chat_key=str(chat_key)[:256],
                        payload_json=(
                            json.dumps(
                                payload,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                default=str,
                            )
                            if payload is not None
                            else None
                        ),
                        status="processing",
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                existing.chat_key = str(chat_key)[:256]
                if payload is not None:
                    existing.payload_json = json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    )
                existing.status = "processing"
                existing.updated_at = now
                existing.completed_at = None
            session.commit()
            return True

    def recover_inbound(self) -> int:
        now = _utcnow()
        with self._lock, self.database.get_session() as session:
            items = list(
                session.execute(
                    select(TelegramInboundReceipt).where(
                        TelegramInboundReceipt.adapter_instance == self.adapter_instance,
                        TelegramInboundReceipt.status == "processing",
                    )
                ).scalars()
            )
            for item in items:
                item.status = INBOUND_RETRYABLE_STATUS
                item.updated_at = now
                item.completed_at = None
            session.commit()
            return len(items)

    def complete_inbound(self, update_id: int | str) -> None:
        with self.database.get_session() as session:
            item = self._inbound(session, update_id)
            item.status = "completed"
            item.payload_json = None
            item.completed_at = _utcnow()
            item.updated_at = item.completed_at
            session.commit()

    def retry_inbound(self, update_id: int | str) -> None:
        with self.database.get_session() as session:
            item = self._inbound(session, update_id)
            if item.status != "completed":
                item.status = INBOUND_RETRYABLE_STATUS
                item.completed_at = None
                item.updated_at = _utcnow()
                session.commit()

    def pending_inbound(self) -> list[tuple[str, dict[str, Any]]]:
        """Return retryable inbound updates that have a durable payload."""
        with self.database.get_session() as session:
            items = list(
                session.execute(
                    select(TelegramInboundReceipt).where(
                        TelegramInboundReceipt.adapter_instance == self.adapter_instance,
                        TelegramInboundReceipt.status == INBOUND_RETRYABLE_STATUS,
                        TelegramInboundReceipt.payload_json.is_not(None),
                    ).order_by(
                        TelegramInboundReceipt.created_at,
                        TelegramInboundReceipt.id,
                    )
                ).scalars()
            )
        pending: list[tuple[str, dict[str, Any]]] = []
        for item in items:
            try:
                payload = json.loads(str(item.payload_json))
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                pending.append((str(item.update_id), payload))
        return pending

    def _inbound(self, session, update_id: int | str) -> TelegramInboundReceipt:
        item = session.execute(
            select(TelegramInboundReceipt).where(
                TelegramInboundReceipt.adapter_instance == self.adapter_instance,
                TelegramInboundReceipt.update_id == str(update_id),
            )
        ).scalar_one_or_none()
        if item is None:
            raise LookupError("Telegram inbound receipt is missing")
        return item

    def enqueue(
        self,
        delivery_id: str,
        recipient_key: str,
        action: str,
        params: dict[str, Any],
        *,
        page_index: int,
        page_count: int,
        logical_delivery_id: Optional[str] = None,
    ) -> TelegramDeliveryResult:
        if not delivery_id or len(delivery_id) > 64:
            raise ValueError("delivery_id must contain 1 to 64 characters")
        if page_index < 0 or page_count < 1 or page_index >= page_count:
            raise ValueError("invalid Telegram page coordinates")
        now = _utcnow()
        with self._lock, self.database.get_session() as session:
            existing = session.execute(
                select(TelegramDelivery).where(
                    TelegramDelivery.delivery_id == delivery_id
                )
            ).scalar_one_or_none()
            if existing is not None:
                return self._snapshot(existing)
            sequence = session.execute(
                select(func.max(TelegramDelivery.recipient_sequence)).where(
                    TelegramDelivery.recipient_key == recipient_key
                )
            ).scalar_one_or_none()
            item = TelegramDelivery(
                delivery_id=delivery_id,
                logical_delivery_id=logical_delivery_id or delivery_id,
                adapter_instance=self.adapter_instance,
                recipient_key=recipient_key,
                recipient_sequence=int(sequence or 0) + 1,
                page_index=page_index,
                page_count=page_count,
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

    def get(self, delivery_id: str) -> TelegramDeliveryResult:
        with self.database.get_session() as session:
            item = session.execute(
                select(TelegramDelivery).where(
                    TelegramDelivery.delivery_id == delivery_id
                )
            ).scalar_one()
            return self._snapshot(item)

    def recover_on_startup(self) -> int:
        now = _utcnow()
        with self.database.get_session() as session:
            items = list(
                session.execute(
                    select(TelegramDelivery).where(
                        TelegramDelivery.adapter_instance == self.adapter_instance,
                        TelegramDelivery.status == "sending",
                    )
                ).scalars()
            )
            for item in items:
                item.status = "ambiguous"
                item.upstream_accepted = False
                item.client_received = None
                item.last_error = (
                    "process stopped while Telegram upstream message result was unknown"
                )
                item.ambiguous_at = now
                item.updated_at = now
            session.commit()
            return len(items)

    def pending_delivery_ids(self) -> list[str]:
        with self.database.get_session() as session:
            return list(
                session.execute(
                    select(TelegramDelivery.delivery_id)
                    .where(
                        TelegramDelivery.adapter_instance == self.adapter_instance,
                        TelegramDelivery.status.in_(PENDING_STATUSES),
                    )
                    .order_by(
                        TelegramDelivery.recipient_key,
                        TelegramDelivery.recipient_sequence,
                    )
                ).scalars()
            )

    def status_counts(self) -> dict[str, int]:
        counts = {
            "queued": 0,
            "retry_wait": 0,
            "sending": 0,
            "accepted": 0,
            "ambiguous": 0,
            "dead_letter": 0,
        }
        with self.database.get_session() as session:
            rows = session.execute(
                select(TelegramDelivery.status, func.count(TelegramDelivery.id)).where(
                    TelegramDelivery.adapter_instance == self.adapter_instance
                ).group_by(TelegramDelivery.status)
            ).all()
        for status, count in rows:
            if status in counts:
                counts[str(status)] = int(count)
        return counts

    async def resume_pending(self) -> list[TelegramDeliveryResult]:
        return list(
            await asyncio.gather(
                *(self.deliver(item) for item in self.pending_delivery_ids())
            )
        )

    async def deliver(self, delivery_id: str) -> TelegramDeliveryResult:
        target = self.get(delivery_id)
        if target.status in TERMINAL_STATUSES:
            return target
        state = self._recipient_locks.setdefault(
            target.recipient_key, _RecipientLockState(lock=asyncio.Lock())
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
        self, target: TelegramDeliveryResult
    ) -> TelegramDeliveryResult:
        # Startup recovery can schedule several pages concurrently. Advance
        # the earliest pending page while holding the per-recipient lock so a
        # later page cannot strand an earlier queued page.
        while True:
            current = self.get(target.delivery_id)
            if current.status in TERMINAL_STATUSES:
                return current
            with self.database.get_session() as session:
                blocker = session.execute(
                    select(TelegramDelivery).where(
                        TelegramDelivery.recipient_key == target.recipient_key,
                        TelegramDelivery.recipient_sequence < current.recipient_sequence,
                        TelegramDelivery.status != "accepted",
                    ).order_by(TelegramDelivery.recipient_sequence).limit(1)
                ).scalar_one_or_none()
                next_delivery = session.execute(
                    select(TelegramDelivery).where(
                        TelegramDelivery.recipient_key == target.recipient_key,
                        TelegramDelivery.recipient_sequence <= current.recipient_sequence,
                        TelegramDelivery.status.in_(PENDING_STATUSES),
                    ).order_by(TelegramDelivery.recipient_sequence).limit(1)
                ).scalar_one_or_none()
                next_id = str(next_delivery.delivery_id) if next_delivery else None
            if blocker is not None and blocker.status in TERMINAL_STATUSES:
                return current
            if next_id is None:
                return self.get(target.delivery_id)
            result = await self._deliver_one(next_id)
            if result.status != "accepted":
                return result

    async def _deliver_one(self, delivery_id: str) -> TelegramDeliveryResult:
        """Send one unit, retrying only what Telegram explicitly asked us to retry.

        `max_attempts` / `retry_delay_seconds` 此前只被赋值、从不被读取：
        `RetryAfter`（Telegram 明确说「稍后再试」）被直接判为 dead letter，
        一次都不重试。字段在、文档在、界面能填，改它却什么都不会发生——
        比「没有这个配置」更糟，后者至少不会让人以为已经配好了。

        三条边界必须区分开：

        - `TelegramRetryableError`（来自 `RetryAfter`）：上游明确要求稍后再试，
          按配置的次数与共享退避计划重试。
        - 其他异常：确定失败，重试只是把同一个错误重复犯几遍。
        - 结果未知（超时 / 网络中断）：**绝不重试**。可能已经发出去了，
          重发就是重复消息。`ambiguous` 与 `dead_letter` 在「都失败了」这一层
          看起来像，但一个是「不知道有没有发」、另一个是「确定没发」，
          处置完全相反。
        """
        while True:
            with self.database.get_session() as session:
                item = session.execute(
                    select(TelegramDelivery).where(
                        TelegramDelivery.delivery_id == delivery_id
                    )
                ).scalar_one()
                if item.status in TERMINAL_STATUSES:
                    return self._snapshot(item)
                item.status = "sending"
                item.attempt_count += 1
                item.updated_at = _utcnow()
                attempt_count = int(item.attempt_count)
                params = json.loads(str(item.params_json))
                session.commit()
            try:
                response = self.sender(params)
                if inspect.isawaitable(response):
                    response = await response
                if response is None:
                    raise asyncio.TimeoutError("Telegram API returned no result")
            except asyncio.CancelledError:
                self._mark_ambiguous(delivery_id, "delivery cancelled during Telegram send")
                raise
            except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
                return self._mark_ambiguous(delivery_id, str(exc), exc)
            except TelegramRetryableError as exc:
                if attempt_count >= self.max_attempts:
                    return self._mark_dead_letter(delivery_id, str(exc), exc)
                delay = retry_backoff_seconds(self.retry_delay_seconds, attempt_count)
                self._mark_retry_wait(delivery_id, str(exc))
                if delay > 0:
                    await asyncio.sleep(delay)
                continue
            except BaseException as exc:
                return self._mark_dead_letter(delivery_id, str(exc), exc)
            return self._mark_accepted(delivery_id, response)

    def _mark_retry_wait(self, delivery_id: str, error: str) -> None:
        """Record why the unit is waiting, without making it terminal."""
        with self.database.get_session() as session:
            item = session.execute(
                select(TelegramDelivery).where(
                    TelegramDelivery.delivery_id == delivery_id
                )
            ).scalar_one()
            item.status = "retry_wait"
            item.last_error = (error or "Telegram delivery rejected")[:2000]
            item.updated_at = _utcnow()
            session.commit()

    def _mark_accepted(self, delivery_id: str, response: Any) -> TelegramDeliveryResult:
        with self.database.get_session() as session:
            item = session.execute(
                select(TelegramDelivery).where(
                    TelegramDelivery.delivery_id == delivery_id
                )
            ).scalar_one()
            item.status = "accepted"
            item.upstream_accepted = True
            item.response_json = json.dumps(
                response, ensure_ascii=False, separators=(",", ":"), default=str
            )
            item.last_error = None
            item.accepted_at = _utcnow()
            item.updated_at = item.accepted_at
            session.commit()
            return self._snapshot(item)

    def _mark_ambiguous(
        self,
        delivery_id: str,
        error: str,
        exception: Optional[BaseException] = None,
    ) -> TelegramDeliveryResult:
        with self.database.get_session() as session:
            item = session.execute(
                select(TelegramDelivery).where(
                    TelegramDelivery.delivery_id == delivery_id
                )
            ).scalar_one()
            item.status = "ambiguous"
            item.last_error = (error or "Telegram upstream result is unknown")[:2000]
            item.ambiguous_at = _utcnow()
            item.updated_at = item.ambiguous_at
            session.commit()
            return self._snapshot(item, exception)

    def _mark_dead_letter(
        self,
        delivery_id: str,
        error: str,
        exception: Optional[BaseException] = None,
    ) -> TelegramDeliveryResult:
        with self.database.get_session() as session:
            item = session.execute(
                select(TelegramDelivery).where(
                    TelegramDelivery.delivery_id == delivery_id
                )
            ).scalar_one()
            item.status = "dead_letter"
            item.last_error = (error or "Telegram delivery failed")[:2000]
            item.updated_at = _utcnow()
            session.commit()
            return self._snapshot(item, exception)


@dataclass
class _RecipientLockState:
    lock: asyncio.Lock
    users: int = 0
