"""Inbound dedup receipts for OneBot and QQBot.

Telegram and WeCom already had this; these two did not. Without a receipt, an
upstream that re-delivers the same event after a reconnect makes the whole
workflow run again — the model is called twice (duplicate cost) and the user gets
two replies. OneBot implementations do re-deliver: a reverse WebSocket that drops
mid-post has no way to know whether we processed the event, so replaying is the
only safe thing *it* can do. The dedup has to live on our side.

The table shape mirrors ``TelegramInboundReceipt`` deliberately, so the four
adapters have one inbound contract rather than four:

- ``claim`` is the gate: the first caller wins, later callers are told to drop.
- A crash leaves rows in ``processing``; ``recover`` re-labels them ``retryable``
  so they can be claimed again after restart rather than being lost or
  double-processed.
- ``complete`` clears the stored payload: a finished event must not keep a copy
  of the message content around.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)

from kirara_ai.database import Base, DatabaseManager

#: A row in this state may be claimed again (crash recovery or explicit retry).
INBOUND_RETRYABLE_STATUS = "retryable"

_MAX_EVENT_KEY_LENGTH = 128
_MAX_CHAT_KEY_LENGTH = 256


class InboundReceipt(Base):
    """One upstream event claimed by one configured adapter instance."""

    __tablename__ = "im_inbound_receipts"
    __table_args__ = (
        UniqueConstraint(
            "channel",
            "adapter_instance",
            "event_key",
            name="uq_im_inbound_channel_adapter_event",
        ),
        Index(
            "idx_im_inbound_status",
            "channel",
            "adapter_instance",
            "status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel = Column(String(32), nullable=False)
    adapter_instance = Column(String(128), nullable=False)
    event_key = Column(String(128), nullable=False)
    chat_key = Column(String(256), nullable=False)
    # Keep a replayable payload only while inbound processing is unfinished.
    payload_json = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="processing", index=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class InboundReceiptService:
    """Claim, complete and recover inbound events for one adapter instance."""

    def __init__(
        self,
        database: DatabaseManager,
        *,
        channel: str,
        adapter_instance: str,
    ) -> None:
        channel_value = str(channel).strip()
        instance_value = str(adapter_instance).strip()
        if not channel_value:
            raise ValueError("channel is required")
        if not instance_value:
            raise ValueError("adapter_instance is required")
        self.database = database
        self.channel = channel_value[:32]
        self.adapter_instance = instance_value[:128]
        self._lock = threading.RLock()

    @staticmethod
    def _utcnow():
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _normalize_event_key(self, event_key: Any) -> str:
        value = str(event_key or "").strip()
        if not value or len(value) > _MAX_EVENT_KEY_LENGTH:
            raise ValueError("inbound event key is invalid")
        return value

    def claim(
        self,
        event_key: Any,
        chat_key: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Return True when this caller owns the event, False when it is a duplicate."""
        key = self._normalize_event_key(event_key)
        now = self._utcnow()
        with self._lock, self.database.get_session() as session:
            existing = session.execute(
                select(InboundReceipt).where(
                    InboundReceipt.channel == self.channel,
                    InboundReceipt.adapter_instance == self.adapter_instance,
                    InboundReceipt.event_key == key,
                )
            ).scalar_one_or_none()
            if existing is not None and existing.status != INBOUND_RETRYABLE_STATUS:
                # Already processed, or being processed right now: drop it.
                return False
            encoded = (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
                if payload is not None
                else None
            )
            if existing is None:
                session.add(
                    InboundReceipt(
                        channel=self.channel,
                        adapter_instance=self.adapter_instance,
                        event_key=key,
                        chat_key=str(chat_key)[:_MAX_CHAT_KEY_LENGTH],
                        payload_json=encoded,
                        status="processing",
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                existing.chat_key = str(chat_key)[:_MAX_CHAT_KEY_LENGTH]
                if encoded is not None:
                    existing.payload_json = encoded
                existing.status = "processing"
                existing.updated_at = now
                existing.completed_at = None
            session.commit()
            return True

    def complete(self, event_key: Any) -> None:
        """Mark the event finished and drop its stored payload."""
        key = self._normalize_event_key(event_key)
        with self._lock, self.database.get_session() as session:
            item = self._get(session, key)
            if item is None:
                return
            item.status = "completed"
            item.payload_json = None
            item.completed_at = self._utcnow()
            item.updated_at = item.completed_at
            session.commit()

    def retry(self, event_key: Any) -> None:
        """Allow the event to be claimed again after a failure."""
        key = self._normalize_event_key(event_key)
        with self._lock, self.database.get_session() as session:
            item = self._get(session, key)
            if item is None or item.status == "completed":
                return
            item.status = INBOUND_RETRYABLE_STATUS
            item.completed_at = None
            item.updated_at = self._utcnow()
            session.commit()

    def recover_on_startup(self) -> int:
        """Re-open events left mid-processing by an interrupted process.

        A crash leaves rows in ``processing``, which would block the event
        forever. Re-labelling them ``retryable`` is the only outcome that neither
        loses the event nor guarantees a double reply: the upstream decides
        whether to redeliver, and if it does, exactly one claim succeeds.
        """
        now = self._utcnow()
        with self._lock, self.database.get_session() as session:
            items = list(
                session.execute(
                    select(InboundReceipt).where(
                        InboundReceipt.channel == self.channel,
                        InboundReceipt.adapter_instance == self.adapter_instance,
                        InboundReceipt.status == "processing",
                    )
                ).scalars()
            )
            for item in items:
                item.status = INBOUND_RETRYABLE_STATUS
                item.updated_at = now
                item.completed_at = None
            session.commit()
            return len(items)

    def status_counts(self) -> dict[str, int]:
        counts = {"processing": 0, INBOUND_RETRYABLE_STATUS: 0, "completed": 0}
        with self.database.get_session() as session:
            rows = session.execute(
                select(InboundReceipt.status).where(
                    InboundReceipt.channel == self.channel,
                    InboundReceipt.adapter_instance == self.adapter_instance,
                )
            ).scalars()
            for status in rows:
                key = str(status)
                if key in counts:
                    counts[key] += 1
        return counts

    def _get(self, session, event_key: str) -> Optional[InboundReceipt]:
        return session.execute(
            select(InboundReceipt).where(
                InboundReceipt.channel == self.channel,
                InboundReceipt.adapter_instance == self.adapter_instance,
                InboundReceipt.event_key == event_key,
            )
        ).scalar_one_or_none()
