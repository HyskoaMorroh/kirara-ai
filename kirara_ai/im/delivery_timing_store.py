"""Durable per-reply delivery timings.

The in-memory timeline on ``IMMessage`` answers "why was *this* reply slow" while
the reply is still in flight, and it can be serialized into a log line. What it
cannot answer is the question an operator actually asks a week later: "QQ felt
slow last Tuesday — was it the model or the send?" That needs rows.

Design constraints that shaped this table:

- **No message content, ever.** Only the channel, a hashed conversation key,
  durations and counts. A latency table must not become a second copy of the
  chat log.
- **A missing measurement stays NULL.** Writing ``0`` for a phase that was never
  observed would be read as "instant", which is the opposite of the truth. A
  non-stream request genuinely has no first-byte time.
- **Bounded retention.** Rows are cleaned on startup like LLM traces, so a
  long-running deployment does not accumulate them forever.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, func, select

from kirara_ai.database import Base, DatabaseManager

#: 默认保留天数，与 LLM 追踪一致。
DEFAULT_RETENTION_DAYS = 30

#: 单次查询的最大返回行数，避免一次把整表读进内存。
MAX_QUERY_LIMIT = 1000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DeliveryTiming(Base):
    """One delivered reply's phase timings. Contains no message content."""

    __tablename__ = "im_delivery_timings"
    __table_args__ = (
        Index("idx_im_delivery_channel_time", "channel", "recorded_at"),
        Index("idx_im_delivery_status_time", "status", "recorded_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel = Column(String(32), nullable=False)
    adapter_instance = Column(String(128), nullable=False)
    #: 会话键的摘要，用于「同一会话是否一直慢」这类聚合；不可反推原始会话。
    conversation_digest = Column(String(64), nullable=True)
    recorded_at = Column(DateTime, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="succeeded")

    #: 各阶段耗时；**未测到的阶段保持 NULL**，绝不写 0。
    queue_seconds = Column(Float, nullable=True)
    llm_first_byte_seconds = Column(Float, nullable=True)
    llm_generation_seconds = Column(Float, nullable=True)
    formatting_seconds = Column(Float, nullable=True)
    send_seconds = Column(Float, nullable=True)
    total_seconds = Column(Float, nullable=True)

    segment_count = Column(Integer, nullable=True)
    retry_count = Column(Integer, nullable=True)
    correlation_id = Column(String(64), nullable=True, index=True)


class DeliveryTimingStore:
    """Persist and query reply latency without storing message content."""

    def __init__(
        self,
        database: DatabaseManager,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        self.database = database
        self.retention_days = retention_days

    @staticmethod
    def _digest(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _positive_or_none(value: Any) -> Optional[float]:
        """Keep a measured duration, drop anything unusable.

        ``None`` 与 ``0`` 必须区分：前者是「没测到这一段」，后者是「确实几乎没耗时」。
        把前者写成 0 会被读成「极快」，与事实相反。
        """
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number < 0:
            return None
        return number

    def record(
        self,
        *,
        channel: str,
        adapter_instance: str,
        durations: Mapping[str, Any],
        status: str = "succeeded",
        conversation_key: Optional[str] = None,
        segment_count: Optional[int] = None,
        retry_count: Optional[int] = None,
        correlation_id: Optional[str] = None,
    ) -> bool:
        """Store one reply's timings. Returns False when there is nothing to store."""
        measured = {
            "queue_seconds": self._positive_or_none(durations.get("queue_seconds")),
            "llm_first_byte_seconds": self._positive_or_none(
                durations.get("llm_first_byte_seconds")
            ),
            "llm_generation_seconds": self._positive_or_none(
                durations.get("llm_generation_seconds")
            ),
            "formatting_seconds": self._positive_or_none(
                durations.get("formatting_seconds")
            ),
            "send_seconds": self._positive_or_none(durations.get("send_seconds")),
            "total_seconds": self._positive_or_none(durations.get("total_seconds")),
        }
        if all(value is None for value in measured.values()):
            # 完全没有可记录的测量值：不写空行。
            return False

        with self.database.get_session() as session:
            session.add(
                DeliveryTiming(
                    channel=str(channel)[:32],
                    adapter_instance=str(adapter_instance)[:128],
                    conversation_digest=self._digest(conversation_key),
                    recorded_at=_utcnow(),
                    status=str(status)[:20],
                    segment_count=segment_count,
                    retry_count=retry_count,
                    correlation_id=(
                        str(correlation_id)[:64] if correlation_id else None
                    ),
                    **measured,
                )
            )
            session.commit()
        return True

    def summarize(
        self,
        *,
        channel: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Aggregate latency per channel over a time range.

        每个阶段的平均值只对**测到该阶段**的行求平均：非流式请求没有首字节，
        把它们按 0 计入会把平均值拉低成一个不存在的数字。
        因此每个阶段同时给出样本数，让读者知道这个平均值代表多少次请求。

        除阶段耗时外还给出 ``counts``（分段数量、重试次数）——需求 19.5 九项里的
        后两项。它们同样只对测到的行求平均，一个都没测到时给 ``None`` 而不是 0。
        """
        phases = (
            "queue_seconds",
            "llm_first_byte_seconds",
            "llm_generation_seconds",
            "formatting_seconds",
            "send_seconds",
            "total_seconds",
        )
        with self.database.get_session() as session:
            query = session.query(DeliveryTiming)
            query = self._apply_filters(query, channel, start_time, end_time)
            total = query.count()

            summary: dict[str, Any] = {"deliveries": total, "phases": {}}
            for phase in phases:
                column = getattr(DeliveryTiming, phase)
                scoped = self._apply_filters(
                    session.query(func.avg(column), func.max(column), func.count(column)),
                    channel,
                    start_time,
                    end_time,
                ).filter(column.isnot(None))
                average, maximum, samples = scoped.one()
                summary["phases"][phase] = {
                    "avg_seconds": float(average) if average is not None else None,
                    "max_seconds": float(maximum) if maximum is not None else None,
                    "samples": int(samples or 0),
                }

            # 分段数量与重试次数是需求 19.5 九项里的后两项。它们不是时间戳，
            # 因此不在 `phases` 里；但落库了却不出现在汇总里等于只能逐条翻——
            # 「上周二那批慢投递是不是因为分了很多页」这个问题回答不了。
            #
            # 口径与阶段耗时完全一致：只对**测到该值**的行求平均并给出样本数。
            # 第三方适配器可能不带 details，那时值为 NULL；把 NULL 按 0 计入会把
            # 平均分段数拉低成一个不存在的数字，而读者无从察觉。
            summary["counts"] = {}
            for name in ("segment_count", "retry_count"):
                column = getattr(DeliveryTiming, name)
                scoped = self._apply_filters(
                    session.query(func.avg(column), func.max(column), func.count(column)),
                    channel,
                    start_time,
                    end_time,
                ).filter(column.isnot(None))
                average, maximum, samples = scoped.one()
                summary["counts"][name] = {
                    # 一个都没测到时给 None 而不是 0：`retry_count: 0` 是一个论断
                    #（「都没重试过」），会让人以为链路一切正常，而实际只是没数据。
                    "avg": float(average) if average is not None else None,
                    "max": int(maximum) if maximum is not None else None,
                    "samples": int(samples or 0),
                }

            failed = self._apply_filters(
                session.query(func.count(DeliveryTiming.id)),
                channel,
                start_time,
                end_time,
            ).filter(DeliveryTiming.status != "succeeded").scalar()
            summary["failed_deliveries"] = int(failed or 0)
        return summary

    def list_channels(self) -> list[str]:
        with self.database.get_session() as session:
            rows = session.execute(
                select(DeliveryTiming.channel).distinct().order_by(DeliveryTiming.channel)
            ).scalars()
            return [str(row) for row in rows]

    def recent(
        self,
        *,
        channel: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > MAX_QUERY_LIMIT:
            raise ValueError("limit is outside the allowed range")
        with self.database.get_session() as session:
            query = session.query(DeliveryTiming)
            if channel:
                query = query.filter(DeliveryTiming.channel == channel)
            rows = (
                query.order_by(DeliveryTiming.recorded_at.desc(), DeliveryTiming.id.desc())
                .limit(limit)
                .all()
            )
            return [self._as_dict(row) for row in rows]

    def cleanup(self, *, now: Optional[datetime] = None) -> int:
        """Delete rows older than the retention window."""
        cutoff = (now or _utcnow()) - timedelta(days=self.retention_days)
        with self.database.get_session() as session:
            deleted = (
                session.query(DeliveryTiming)
                .filter(DeliveryTiming.recorded_at < cutoff)
                .delete()
            )
            session.commit()
            return int(deleted or 0)

    @staticmethod
    def _apply_filters(query, channel, start_time, end_time):
        if channel:
            query = query.filter(DeliveryTiming.channel == channel)
        if start_time is not None:
            query = query.filter(DeliveryTiming.recorded_at >= start_time)
        if end_time is not None:
            query = query.filter(DeliveryTiming.recorded_at < end_time)
        return query

    @staticmethod
    def _as_dict(row: DeliveryTiming) -> dict[str, Any]:
        return {
            "id": int(row.id),
            "channel": str(row.channel),
            "adapter_instance": str(row.adapter_instance),
            "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
            "status": str(row.status),
            "queue_seconds": row.queue_seconds,
            "llm_first_byte_seconds": row.llm_first_byte_seconds,
            "llm_generation_seconds": row.llm_generation_seconds,
            "formatting_seconds": row.formatting_seconds,
            "send_seconds": row.send_seconds,
            "total_seconds": row.total_seconds,
            "segment_count": row.segment_count,
            "retry_count": row.retry_count,
            "correlation_id": row.correlation_id,
        }


def iter_phase_names() -> Iterable[str]:
    return (
        "queue_seconds",
        "llm_first_byte_seconds",
        "llm_generation_seconds",
        "formatting_seconds",
        "send_seconds",
        "total_seconds",
    )
