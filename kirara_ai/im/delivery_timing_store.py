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
    #: 发送段的两个分量（需求 19.5：发送限流不能与「上游真的慢」混成一个「QQ 慢」）。
    #:
    #: ``send_seconds`` 是整段墙钟时间——它回答「用户等了多久」，必须保留。
    #: 但它回答不了「该去查谁」：一条十页回复因防刷屏节流等了 20 秒，
    #: 显示成「平台发送 20 秒」会让运维去查 QQ，而 QQ 什么问题都没有。
    #: 两者处置相反：节流要调 ``send_pacing`` 配置，上游慢要查上游。
    #:
    #: 与其余列同一条口径：**没测到就是 NULL**。没有节流概念的渠道
    #: （Telegram / WeCom）与第三方适配器这两列为空，而 ``0`` 是
    #: 「测了，这次没等」——两者在排查时的含义完全不同。
    send_pacing_seconds = Column(Float, nullable=True)
    send_upstream_seconds = Column(Float, nullable=True)
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

    #: `_positive_or_none` 的同义实现，保留独立名字是为了让调用点自我说明：
    #: 用它的那两列（节流归因）里 `0.0` 是一个**有效测量**，不是「没测到」。
    #: 两者当前行为相同，但含义不同——将来若要给「没测到」加别的判据，
    #: 改的是其中一个而不是两个。
    _zero_aware_or_none = _positive_or_none

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
            # 节流两列用 `_zero_aware_or_none`：`0.0` 是一个有效测量
            #（「节流开着，这一次没等」），而 `_positive_or_none` 会把它当成没测到。
            # 这个区别正是这两列存在的理由——把「测了是 0」丢成 NULL
            # 会让运维无法排除节流这个原因。
            "send_pacing_seconds": self._zero_aware_or_none(
                durations.get("send_pacing_seconds")
            ),
            "send_upstream_seconds": self._zero_aware_or_none(
                durations.get("send_upstream_seconds")
            ),
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

        单渠道视角。跨渠道的**可比**视图见 :meth:`compare`——19.5 的最后一句要求
        「给出 Telegram、WeCom 与 QQ 的可比链路耗时」，而靠切换本方法的 ``channel``
        参数得到的是三次独立查询，对比这件事被推给了读者的短期记忆。
        """
        phases = self.PHASE_COLUMNS
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
            for name in self.COUNT_COLUMNS:
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

    #: 参与汇总与对比的阶段列，顺序即链路顺序。
    #:
    #: 提到类级别是因为 `summarize()` 与 `compare()` 必须用同一份列表：
    #: 两处各写一份时，新增一个阶段只会出现在其中一个视图里，
    #: 而「单渠道视图有这一段、对比视图没有」是一个读者无从解释的差异。
    PHASE_COLUMNS = (
        "queue_seconds",
        "llm_first_byte_seconds",
        "llm_generation_seconds",
        "formatting_seconds",
        "send_seconds",
        # 发送段的两个分量紧跟在整段之后：读者先看到「用户等了多久」，
        # 再看到它由哪两部分构成。
        "send_pacing_seconds",
        "send_upstream_seconds",
        "total_seconds",
    )

    #: 参与汇总与对比的计数列（需求 19.5 九项里的后两项）。
    COUNT_COLUMNS = ("segment_count", "retry_count")

    def compare(
        self,
        *,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        """Aggregate the same phases for **every** channel in one pass.

        19.5 的最后一句是硬要求：「应给出 Telegram、WeCom 与 QQ 的**可比**链路耗时」。
        :meth:`summarize` 只接受一个 ``channel``，界面上是一个下拉筛选器——要比较三个
        渠道，运维得切三次下拉框再靠记忆对比六个阶段的数字。那不是可比，那是把对比
        推给人的短期记忆。而 19.5 要回答的问题恰恰是对比式的：「QQ 慢，是 QQ 这条
        链路慢，还是模型本来就慢（三个渠道一样慢）」——没有对照组时这个问题
        无法回答，而单渠道视图永远给不出对照组。

        口径与 :meth:`summarize` 逐字一致：只对**测到该阶段**的行求平均、每项带样本数、
        一个都没测到时给 ``None`` 而不是 0。在对比视图里这条比单渠道时更要紧——
        一个渠道显示 0 ms 首字节、另一个显示 2 s 时，看起来是前者快得多，
        而事实是前者根本没测。

        **一次分组查询，不是每个渠道各查一遍。** 六个阶段 × N 个渠道 = 6N 次查询，
        而这张表在长期运行的部署上会长（默认保留 30 天）；N+1 查询在这种表上是一个
        会随时间变慢的设计，慢下来的时候正好是最需要它的时候。
        ``idx_im_delivery_channel_time`` 覆盖 ``(channel, recorded_at)``，分组聚合走它。
        """
        rows: dict[str, dict[str, Any]] = {}

        def bucket(channel: str) -> dict[str, Any]:
            entry = rows.get(channel)
            if entry is None:
                entry = rows[channel] = {
                    "channel": channel,
                    "deliveries": 0,
                    "failed_deliveries": 0,
                    "phases": {},
                    "counts": {},
                }
            return entry

        with self.database.get_session() as session:
            totals = self._apply_filters(
                session.query(
                    DeliveryTiming.channel,
                    func.count(DeliveryTiming.id),
                ),
                None,
                start_time,
                end_time,
            ).group_by(DeliveryTiming.channel)
            for channel, count in totals:
                bucket(str(channel))["deliveries"] = int(count or 0)

            failures = self._apply_filters(
                session.query(
                    DeliveryTiming.channel,
                    func.count(DeliveryTiming.id),
                ),
                None,
                start_time,
                end_time,
            ).filter(DeliveryTiming.status != "succeeded").group_by(
                DeliveryTiming.channel
            )
            for channel, count in failures:
                bucket(str(channel))["failed_deliveries"] = int(count or 0)

            for phase in self.PHASE_COLUMNS:
                column = getattr(DeliveryTiming, phase)
                grouped = self._apply_filters(
                    session.query(
                        DeliveryTiming.channel,
                        func.avg(column),
                        func.max(column),
                        func.count(column),
                    ),
                    None,
                    start_time,
                    end_time,
                ).filter(column.isnot(None)).group_by(DeliveryTiming.channel)
                measured = {
                    str(channel): (average, maximum, samples)
                    for channel, average, maximum, samples in grouped
                }
                for entry in rows.values():
                    average, maximum, samples = measured.get(
                        entry["channel"], (None, None, 0)
                    )
                    entry["phases"][phase] = {
                        "avg_seconds": float(average) if average is not None else None,
                        "max_seconds": float(maximum) if maximum is not None else None,
                        "samples": int(samples or 0),
                    }

            for name in self.COUNT_COLUMNS:
                column = getattr(DeliveryTiming, name)
                grouped = self._apply_filters(
                    session.query(
                        DeliveryTiming.channel,
                        func.avg(column),
                        func.max(column),
                        func.count(column),
                    ),
                    None,
                    start_time,
                    end_time,
                ).filter(column.isnot(None)).group_by(DeliveryTiming.channel)
                measured = {
                    str(channel): (average, maximum, samples)
                    for channel, average, maximum, samples in grouped
                }
                for entry in rows.values():
                    average, maximum, samples = measured.get(
                        entry["channel"], (None, None, 0)
                    )
                    entry["counts"][name] = {
                        "avg": float(average) if average is not None else None,
                        "max": int(maximum) if maximum is not None else None,
                        "samples": int(samples or 0),
                    }

        # 渠道名升序：顺序稳定，两次查看同一时间范围得到同一张表。
        return [rows[channel] for channel in sorted(rows)]

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
            "send_pacing_seconds": row.send_pacing_seconds,
            "send_upstream_seconds": row.send_upstream_seconds,
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
