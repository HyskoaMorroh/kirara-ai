"""Persistent, recipient-ordered delivery queue for QQ Bot replies."""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)

from kirara_ai.database import Base, DatabaseManager


TERMINAL_STATUSES = frozenset({"accepted", "ambiguous", "dead_letter"})
PENDING_STATUSES = frozenset({"queued", "retry_wait", "uploaded"})


class QQBotRetryableError(RuntimeError):
    """The QQ API explicitly rejected an operation before accepting it."""


class QQBotAmbiguousError(RuntimeError):
    """The result of a QQ API operation cannot be determined safely."""


@dataclass(frozen=True)
class QQBotDeliveryResult:
    id: int
    delivery_id: str
    logical_delivery_id: str
    recipient_key: str
    recipient_sequence: int
    status: str
    attempt_count: int
    upload_attempt_count: int
    upstream_accepted: bool
    client_received: Optional[bool]
    media_uploaded: bool
    error_message: Optional[str]
    error: Optional[BaseException] = None


class QQBotDelivery(Base):
    """One independently recoverable QQ message or media message."""

    __tablename__ = "qqbot_outbox_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "recipient_key",
            "recipient_sequence",
            name="uq_qqbot_outbox_recipient_sequence",
        ),
        Index(
            "idx_qqbot_outbox_recipient_status_sequence",
            "recipient_key",
            "status",
            "recipient_sequence",
        ),
        Index("idx_qqbot_outbox_status_next_attempt", "status", "next_attempt_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    delivery_id = Column(String(64), nullable=False, unique=True, index=True)
    logical_delivery_id = Column(String(64), nullable=False, index=True)
    recipient_key = Column(String(256), nullable=False, index=True)
    recipient_sequence = Column(Integer, nullable=False)
    action = Column(String(64), nullable=False)
    params_json = Column(Text, nullable=False)
    media_file_type = Column(Integer, nullable=True)
    media_data = Column(LargeBinary, nullable=True)
    media_response_json = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="queued", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    upload_attempt_count = Column(Integer, nullable=False, default=0)
    upstream_accepted = Column(Boolean, nullable=False, default=False)
    client_received = Column(Boolean, nullable=True)
    response_json = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    next_attempt_at = Column(DateTime, nullable=True)
    uploaded_at = Column(DateTime, nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    ambiguous_at = Column(DateTime, nullable=True)


Sender = Callable[[dict[str, Any]], Awaitable[Any] | Any]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class QQBotOutboxService:
    """Persist QQ actions and avoid replaying a message with unknown outcome."""

    def __init__(
        self,
        database: DatabaseManager,
        sender: Sender,
        *,
        media_uploader: Optional[Sender] = None,
        max_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        self.database = database
        self.sender = sender
        self.media_uploader = media_uploader
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self._enqueue_lock = threading.RLock()
        self._recipient_locks: dict[str, _RecipientLockState] = {}

    @staticmethod
    def _snapshot(
        delivery: QQBotDelivery,
        error: Optional[BaseException] = None,
    ) -> QQBotDeliveryResult:
        return QQBotDeliveryResult(
            id=int(delivery.id),
            delivery_id=str(delivery.delivery_id),
            logical_delivery_id=str(delivery.logical_delivery_id),
            recipient_key=str(delivery.recipient_key),
            recipient_sequence=int(delivery.recipient_sequence),
            status=str(delivery.status),
            attempt_count=int(delivery.attempt_count),
            upload_attempt_count=int(delivery.upload_attempt_count),
            upstream_accepted=bool(delivery.upstream_accepted),
            client_received=delivery.client_received,
            media_uploaded=delivery.media_response_json is not None,
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
        media_file_type: Optional[int] = None,
        media_data: Optional[bytes] = None,
    ) -> QQBotDeliveryResult:
        if not delivery_id or len(delivery_id) > 64:
            raise ValueError("delivery_id must contain 1 to 64 characters")
        if not recipient_key or len(recipient_key) > 256:
            raise ValueError("recipient_key must contain 1 to 256 characters")
        if (media_file_type is None) != (media_data is None):
            raise ValueError("media_file_type and media_data must be provided together")
        if media_data is not None and not isinstance(media_data, bytes):
            raise ValueError("media_data must be bytes")
        encoded_params = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
        logical_id = logical_delivery_id or delivery_id
        now = _utcnow()

        with self._enqueue_lock, self.database.get_session() as session:
            existing = session.execute(
                select(QQBotDelivery).where(QQBotDelivery.delivery_id == delivery_id)
            ).scalar_one_or_none()
            if existing is not None:
                return self._snapshot(existing)

            sequence = session.execute(
                select(func.max(QQBotDelivery.recipient_sequence)).where(
                    QQBotDelivery.recipient_key == recipient_key
                )
            ).scalar_one_or_none()
            delivery = QQBotDelivery(
                delivery_id=delivery_id,
                logical_delivery_id=logical_id,
                recipient_key=recipient_key,
                recipient_sequence=int(sequence or 0) + 1,
                action=action,
                params_json=encoded_params,
                media_file_type=media_file_type,
                media_data=media_data,
                status="queued",
                attempt_count=0,
                upload_attempt_count=0,
                upstream_accepted=False,
                client_received=None,
                created_at=now,
                updated_at=now,
            )
            session.add(delivery)
            session.commit()
            return self._snapshot(delivery)

    def get(self, delivery_id: str) -> QQBotDeliveryResult:
        with self.database.get_session() as session:
            delivery = session.execute(
                select(QQBotDelivery).where(QQBotDelivery.delivery_id == delivery_id)
            ).scalar_one()
            return self._snapshot(delivery)

    def recover_on_startup(self) -> int:
        """Quarantine message sends; an interrupted media upload is safe to retry."""
        now = _utcnow()
        recovered = 0
        with self.database.get_session() as session:
            deliveries = list(
                session.execute(
                    select(QQBotDelivery).where(
                        QQBotDelivery.status.in_({"sending", "uploading"})
                    )
                ).scalars()
            )
            for delivery in deliveries:
                if delivery.status == "sending":
                    delivery.status = "ambiguous"
                    delivery.upstream_accepted = False
                    delivery.client_received = None
                    delivery.last_error = (
                        "process stopped while QQ upstream message result was unknown"
                    )
                    delivery.ambiguous_at = now
                    recovered += 1
                else:
                    delivery.status = "queued"
                    delivery.last_error = (
                        "process stopped during media upload; upload can be resumed"
                    )
                delivery.updated_at = now
            session.commit()
            return recovered

    def pending_delivery_ids(self) -> list[str]:
        with self.database.get_session() as session:
            return list(
                session.execute(
                    select(QQBotDelivery.delivery_id)
                    .where(QQBotDelivery.status.in_(PENDING_STATUSES))
                    .order_by(
                        QQBotDelivery.recipient_key,
                        QQBotDelivery.recipient_sequence,
                    )
                ).scalars()
            )

    def status_counts(self) -> dict[str, int]:
        counts = {
            "queued": 0,
            "uploading": 0,
            "uploaded": 0,
            "sending": 0,
            "accepted": 0,
            "retry_wait": 0,
            "ambiguous": 0,
            "dead_letter": 0,
        }
        with self.database.get_session() as session:
            rows = session.execute(
                select(QQBotDelivery.status, func.count(QQBotDelivery.id)).group_by(
                    QQBotDelivery.status
                )
            ).all()
        for status, count in rows:
            if status in counts:
                counts[str(status)] = int(count)
        return counts

    async def resume_pending(self) -> list[QQBotDeliveryResult]:
        return list(await asyncio.gather(*(self.deliver(item) for item in self.pending_delivery_ids())))

    async def deliver(self, delivery_id: str) -> QQBotDeliveryResult:
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
        self, target: QQBotDeliveryResult
    ) -> QQBotDeliveryResult:
        while True:
            target_now = self.get(target.delivery_id)
            if target_now.status in TERMINAL_STATUSES:
                return target_now
            with self.database.get_session() as session:
                blocker = session.execute(
                    select(QQBotDelivery)
                    .where(
                        QQBotDelivery.recipient_key == target.recipient_key,
                        QQBotDelivery.recipient_sequence < target.recipient_sequence,
                        QQBotDelivery.status != "accepted",
                    )
                    .order_by(QQBotDelivery.recipient_sequence)
                    .limit(1)
                ).scalar_one_or_none()
                if blocker is not None and blocker.status in {"ambiguous", "dead_letter"}:
                    return target_now
                next_delivery = session.execute(
                    select(QQBotDelivery)
                    .where(
                        QQBotDelivery.recipient_key == target.recipient_key,
                        QQBotDelivery.recipient_sequence <= target.recipient_sequence,
                        QQBotDelivery.status.in_(PENDING_STATUSES),
                    )
                    .order_by(QQBotDelivery.recipient_sequence)
                    .limit(1)
                ).scalar_one_or_none()
                next_id = str(next_delivery.delivery_id) if next_delivery else None
            if next_id is None:
                return self.get(target.delivery_id)
            result = await self._deliver_one(next_id)
            if result.status != "accepted":
                return result

    async def _deliver_one(self, delivery_id: str) -> QQBotDeliveryResult:
        while True:
            delay = 0.0
            with self.database.get_session() as session:
                delivery = session.execute(
                    select(QQBotDelivery).where(QQBotDelivery.delivery_id == delivery_id)
                ).scalar_one()
                if delivery.status in TERMINAL_STATUSES:
                    return self._snapshot(delivery)
                if delivery.status == "retry_wait" and delivery.next_attempt_at:
                    delay = max(0.0, (delivery.next_attempt_at - _utcnow()).total_seconds())
                params = json.loads(str(delivery.params_json))
                has_media = delivery.media_file_type is not None

            if delay:
                await asyncio.sleep(delay)

            with self.database.get_session() as session:
                delivery = session.execute(
                    select(QQBotDelivery).where(QQBotDelivery.delivery_id == delivery_id)
                ).scalar_one()
                if delivery.status in TERMINAL_STATUSES:
                    return self._snapshot(delivery)
                if delivery.status == "retry_wait":
                    delivery.status = "queued" if delivery.media_response_json is None else "uploaded"
                if has_media and delivery.media_response_json is None:
                    delivery.status = "uploading"
                    delivery.upload_attempt_count += 1
                    attempt_count = int(delivery.upload_attempt_count)
                    phase = "upload"
                else:
                    delivery.status = "sending"
                    delivery.attempt_count += 1
                    attempt_count = int(delivery.attempt_count)
                    phase = "send"
                delivery.next_attempt_at = None
                delivery.updated_at = _utcnow()
                media_type = delivery.media_file_type
                media_data = delivery.media_data
                media_response = (
                    json.loads(str(delivery.media_response_json))
                    if delivery.media_response_json
                    else None
                )
                session.commit()

            try:
                if phase == "upload":
                    if self.media_uploader is None or media_type is None or media_data is None:
                        return self._mark_dead_letter(
                            delivery_id, "media uploader is not configured"
                        )
                    upload_params = dict(params)
                    upload_params.update({"file_type": media_type, "file_data": media_data})
                    response = self.media_uploader(upload_params)
                    if inspect.isawaitable(response):
                        response = await response
                    if response is None:
                        raise QQBotAmbiguousError("QQ media upload returned no result")
                    self._mark_uploaded(delivery_id, response)
                    continue

                send_params = dict(params)
                if media_response is not None:
                    send_params["media"] = media_response
                response = self.sender(send_params)
                if inspect.isawaitable(response):
                    response = await response
                if response is None:
                    raise QQBotAmbiguousError("QQ message API returned no result")
            except asyncio.CancelledError:
                if phase == "send":
                    self._mark_ambiguous(delivery_id, "delivery cancelled during QQ message send")
                else:
                    self._mark_upload_retry(delivery_id, "media upload cancelled")
                raise
            except (asyncio.TimeoutError, QQBotAmbiguousError, ConnectionError, OSError) as exc:
                if phase == "send":
                    return self._mark_ambiguous(delivery_id, str(exc), exc)
                return self._mark_upload_retry(delivery_id, str(exc), exc)
            except QQBotRetryableError as exc:
                if attempt_count >= self.max_attempts:
                    return self._mark_dead_letter(delivery_id, str(exc), exc)
                self._mark_retry_wait(delivery_id, str(exc), phase)
                continue
            except BaseException as exc:
                return self._mark_dead_letter(delivery_id, str(exc), exc)
            return self._mark_accepted(delivery_id, response)

    def _mark_uploaded(self, delivery_id: str, response: Any) -> None:
        now = _utcnow()
        with self.database.get_session() as session:
            delivery = session.execute(
                select(QQBotDelivery).where(QQBotDelivery.delivery_id == delivery_id)
            ).scalar_one()
            delivery.status = "uploaded"
            delivery.media_response_json = json.dumps(
                response, ensure_ascii=False, separators=(",", ":"), default=str
            )
            delivery.last_error = None
            delivery.uploaded_at = now
            delivery.updated_at = now
            session.commit()

    def _mark_upload_retry(
        self,
        delivery_id: str,
        error: str,
        exception: Optional[BaseException] = None,
    ) -> QQBotDeliveryResult:
        with self.database.get_session() as session:
            delivery = session.execute(
                select(QQBotDelivery).where(QQBotDelivery.delivery_id == delivery_id)
            ).scalar_one()
            delivery.status = "queued"
            delivery.last_error = (error or "media upload failed")[:2000]
            delivery.updated_at = _utcnow()
            session.commit()
            return self._snapshot(delivery, exception)

    def _mark_retry_wait(self, delivery_id: str, error: str, phase: str) -> None:
        now = _utcnow()
        with self.database.get_session() as session:
            delivery = session.execute(
                select(QQBotDelivery).where(QQBotDelivery.delivery_id == delivery_id)
            ).scalar_one()
            count = delivery.upload_attempt_count if phase == "upload" else delivery.attempt_count
            delay = self.retry_delay_seconds * (2 ** max(0, int(count) - 1))
            delivery.status = "retry_wait"
            delivery.last_error = error[:2000]
            delivery.next_attempt_at = now + timedelta(seconds=delay)
            delivery.updated_at = now
            session.commit()

    def _mark_accepted(self, delivery_id: str, response: Any) -> QQBotDeliveryResult:
        now = _utcnow()
        with self.database.get_session() as session:
            delivery = session.execute(
                select(QQBotDelivery).where(QQBotDelivery.delivery_id == delivery_id)
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

    def _mark_ambiguous(
        self,
        delivery_id: str,
        error: str,
        exception: Optional[BaseException] = None,
    ) -> QQBotDeliveryResult:
        now = _utcnow()
        with self.database.get_session() as session:
            delivery = session.execute(
                select(QQBotDelivery).where(QQBotDelivery.delivery_id == delivery_id)
            ).scalar_one()
            delivery.status = "ambiguous"
            delivery.upstream_accepted = False
            delivery.client_received = None
            delivery.last_error = (error or "QQ upstream result is unknown")[:2000]
            delivery.ambiguous_at = now
            delivery.updated_at = now
            session.commit()
            return self._snapshot(delivery, exception)

    def _mark_dead_letter(
        self,
        delivery_id: str,
        error: str,
        exception: Optional[BaseException] = None,
    ) -> QQBotDeliveryResult:
        with self.database.get_session() as session:
            delivery = session.execute(
                select(QQBotDelivery).where(QQBotDelivery.delivery_id == delivery_id)
            ).scalar_one()
            delivery.status = "dead_letter"
            delivery.upstream_accepted = False
            delivery.client_received = None
            delivery.last_error = (error or "QQ delivery failed")[:2000]
            delivery.updated_at = _utcnow()
            session.commit()
            return self._snapshot(delivery, exception)


@dataclass
class _RecipientLockState:
    lock: asyncio.Lock
    users: int = 0

