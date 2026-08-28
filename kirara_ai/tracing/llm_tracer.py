from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Dict, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import case, func

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.events.tracing import LLMRequestCompleteEvent, LLMRequestFailEvent, LLMRequestStartEvent
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.pricing import CostSnapshot
from kirara_ai.llm.resilience import ProviderAttempt
from kirara_ai.logger import get_logger
from kirara_ai.tracing.core import TracerBase, generate_trace_id
from kirara_ai.tracing.models import LLMRequestTrace

logger = get_logger("LLMTracer")

UNRECORD_REQUEST = [LLMChatMessage(
    role="system",
    content=[
        LLMChatTextContent(
            text="*** 内容未记录 ***"
        )
    ]
)]

UNRECORD_RESPONSE = Message(
    role="assistant",
    content=[
        LLMChatTextContent(
            text="*** 内容未记录 ***"
        )
    ]
)

class LLMTracer(TracerBase[LLMRequestTrace]):
    """LLM追踪器，负责处理LLM请求的跟踪"""

    name = "llm"
    record_class = LLMRequestTrace

    @Inject()
    def __init__(self, container: DependencyContainer):
        super().__init__(container, record_class=LLMRequestTrace) # type: ignore
        self.config = container.resolve(GlobalConfig)
        
    def initialize(self):
        """启动追踪器，将所有 pending 状态的任务转为 failed，并清理超过 30 天的请求"""
        super().initialize()
        
        try:
            pending_traces = self._mark_pending_as_failed()
            deleted_count = self._clean_old_traces()
            if pending_traces or deleted_count:
                self.logger.info(f"已将 {pending_traces} 个 未结束状态的 LLM 请求标记为失败，并清理了 {deleted_count} 个超过 30 天的请求记录")
        except Exception as e:
            self.logger.opt(exception=e).error(f"处理历史追踪记录时发生错误")

    def _mark_pending_as_failed(self) -> int:
        """将所有 pending 状态的任务转为 failed"""
        with self.db_manager.get_session() as session:
            pending_traces = session.query(LLMRequestTrace).filter(
                LLMRequestTrace.status == "pending" # type: ignore
            ).all()
            for trace in pending_traces:
                trace.status = "failed" # type: ignore
                trace.error = "Incomplete request" # type: ignore
                trace.error_category = "unknown" # type: ignore
            session.commit()
            return len(pending_traces)
            
    def _clean_old_traces(self, days: int = 30) -> int:
        """清理超过指定天数的请求"""
        with self.db_manager.get_session() as session:
            days_ago = datetime.now() - timedelta(days=days)
            deleted_count = session.query(LLMRequestTrace).filter(
                LLMRequestTrace.request_time < days_ago # type: ignore
            ).delete()
            session.commit()
            return deleted_count

    def _register_event_handlers(self):
        """注册事件处理程序"""
        self.event_bus.register(LLMRequestStartEvent, self._on_request_start)
        self.event_bus.register(LLMRequestCompleteEvent, self._on_request_complete)
        self.event_bus.register(LLMRequestFailEvent, self._on_request_fail)

    def _unregister_event_handlers(self):
        """取消事件处理程序注册"""
        self.event_bus.unregister(LLMRequestStartEvent, self._on_request_start)
        self.event_bus.unregister(LLMRequestCompleteEvent, self._on_request_complete)
        self.event_bus.unregister(LLMRequestFailEvent, self._on_request_fail)

    def start_request_tracking(
        self,
        backend_name: str,
        request: LLMChatRequest,
        *,
        correlation_id: Optional[str] = None,
    ) -> str:
        """开始跟踪LLM请求"""
        trace_id = generate_trace_id()
        event = LLMRequestStartEvent(
            trace_id=trace_id,
            model_id=request.model or 'unknown',
            backend_name=backend_name,
            request=request.model_copy(deep=True),
            correlation_id=correlation_id,
        )
        # 存储活跃追踪信息
        self._active_traces[trace_id] = {
            'backend_name': backend_name,
            'start_time': event.start_time,
            'correlation_id': correlation_id,
        }
        # 发布事件
        self.event_bus.post(event)
        return trace_id

    def complete_request_tracking(
        self,
        trace_id: str,
        request: LLMChatRequest,
        response: LLMChatResponse,
        *,
        attempts: Optional[Iterable[ProviderAttempt]] = None,
        cost_snapshot: Optional[CostSnapshot] = None,
        ttft_ms: Optional[int] = None,
        backend_name: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ):
        """完成LLM请求跟踪"""
        if trace_id in self._active_traces:
            trace_data = self._active_traces[trace_id]
            model_id = request.model or trace_data.get('model_id', "unknown")
            backend_name = backend_name or trace_data.get('backend_name', "unknown")
            start_time = trace_data.get('start_time', 0)

            event = LLMRequestCompleteEvent(
                trace_id=trace_id,
                model_id=model_id,
                backend_name=backend_name,
                request=request.model_copy(deep=True),
                response=response.model_copy(deep=True),
                start_time=start_time,
                attempts=attempts,
                cost_snapshot=cost_snapshot,
                ttft_ms=ttft_ms,
                correlation_id=(
                    correlation_id
                    if correlation_id is not None
                    else trace_data.get('correlation_id')
                ),
            )
            # 移除活跃追踪
            del self._active_traces[trace_id]
            # 发布事件
            self.event_bus.post(event)

    def fail_request_tracking(
        self,
        trace_id: str,
        request: LLMChatRequest,
        error: Any,
        *,
        attempts: Optional[Iterable[ProviderAttempt]] = None,
        ttft_ms: Optional[int] = None,
        backend_name: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ):
        """记录LLM请求失败"""
        if trace_id in self._active_traces:
            trace_data = self._active_traces[trace_id]
            model_id = request.model or trace_data.get('model_id', "unknown")
            backend_name = backend_name or trace_data.get('backend_name', "unknown")
            start_time = trace_data.get('start_time', 0)

            event = LLMRequestFailEvent(
                trace_id=trace_id,
                model_id=model_id,
                backend_name=backend_name,
                request=request.model_copy(deep=True),
                error=error,
                start_time=start_time,
                attempts=attempts,
                ttft_ms=ttft_ms,
                correlation_id=(
                    correlation_id
                    if correlation_id is not None
                    else trace_data.get('correlation_id')
                ),
            )
            # 移除活跃追踪
            del self._active_traces[trace_id]
            # 发布事件
            self.event_bus.post(event)
        else:
            self.logger.warning(f"LLM request failed: {trace_id} not found")

    def _on_request_start(self, event: LLMRequestStartEvent):
        """处理请求开始事件"""
        self.logger.debug(f"LLM request started: {event.trace_id}")
        if not self.config.tracing.llm_tracing_content:
            event.request.messages = UNRECORD_REQUEST

        # 创建数据库记录
        trace = LLMRequestTrace()
        trace.update_from_event(event)

        # 保存记录到数据库
        trace_dict = self.save_trace_record(trace)

        # 向WebSocket客户端广播消息
        self.broadcast_ws_message({
            "type": "new",
            "data": trace_dict
        })

    def _on_request_complete(self, event: LLMRequestCompleteEvent):
        """处理请求完成事件"""
        self.logger.debug(f"LLM request completed: {event.trace_id}")

        if not self.config.tracing.llm_tracing_content:
            event.request.messages = UNRECORD_REQUEST
            event.response.message = UNRECORD_RESPONSE
        if trace := self.update_trace_record(event.trace_id, event):
            self.broadcast_ws_message({
                "type": "update",
                "data": trace
            })

    def _on_request_fail(self, event: LLMRequestFailEvent):
        """处理请求失败事件"""
        self.logger.debug(f"LLM request failed: {event.trace_id}")
        
        if not self.config.tracing.llm_tracing_content:
            event.request.messages = UNRECORD_REQUEST

        # 更新数据库记录
        trace = self.update_trace_record(event.trace_id, event)

        # 广播WebSocket消息
        if trace:
            self.broadcast_ws_message({
                "type": "update",
                "data": trace
            })

    @staticmethod
    def _apply_statistics_filters(query, filters: Mapping[str, Any]):
        for field, value in filters.items():
            if value is None:
                continue
            if field == "start_time":
                query = query.filter(LLMRequestTrace.request_time >= value)  # type: ignore
            elif field == "end_time":
                query = query.filter(LLMRequestTrace.request_time < value)  # type: ignore
            elif hasattr(LLMRequestTrace, field):
                query = query.filter(getattr(LLMRequestTrace, field) == value)
        return query

    @staticmethod
    def _group_statistics(query, column, key: str) -> list[Dict[str, Any]]:
        """按维度聚合请求数、Token、平均耗时与成本。

        成本存在 ``cost_snapshot_json`` 里——历史请求必须沿用当时的定价快照，
        不能拿现价重算——所以无法用 SQL 的 ``SUM`` 完成，这里改为取出该维度所需的
        原始列后在 Python 侧聚合。排序仍保持「按请求数降序、维度键升序」。
        """
        rows = query.with_entities(
            column,
            LLMRequestTrace.total_tokens,
            LLMRequestTrace.duration,
            LLMRequestTrace.cost_snapshot_json,
        ).all()
        grouped: dict[Any, dict[str, Any]] = {}
        for value, tokens, duration, snapshot_json in rows:
            bucket = grouped.setdefault(
                value,
                {
                    key: value,
                    "count": 0,
                    "tokens": 0,
                    "avg_duration": 0.0,
                    "_duration_total": 0.0,
                    "_duration_samples": 0,
                    "cost": Decimal("0"),
                    "unpriced_requests": 0,
                },
            )
            bucket["count"] += 1
            bucket["tokens"] += int(tokens or 0)
            if duration is not None:
                bucket["_duration_total"] += float(duration)
                bucket["_duration_samples"] += 1
            cost = LLMTracer._snapshot_cost(snapshot_json)
            if cost is None:
                bucket["unpriced_requests"] += 1
            else:
                bucket["cost"] += cost

        results = []
        for bucket in grouped.values():
            samples = bucket.pop("_duration_samples")
            total = bucket.pop("_duration_total")
            bucket["avg_duration"] = total / samples if samples else 0.0
            bucket["cost"] = str(bucket["cost"])
            results.append(bucket)
        results.sort(key=lambda row: (-row["count"], str(row[key] or "")))
        return results

    @staticmethod
    def _snapshot_cost(snapshot_json: Optional[str]) -> Optional[Decimal]:
        """从价格快照里取出总成本；没有快照就返回 ``None``。

        ``None`` 与 ``0`` 必须区分：前者是「这条请求没有定价证据」，
        后者是「定价过且确实免费」。把前者当 0 会让账单凭空变小。
        """
        if not snapshot_json:
            return None
        try:
            payload = json.loads(snapshot_json)
        except (TypeError, ValueError):
            return None
        raw = payload.get("total_cost") if isinstance(payload, dict) else None
        if raw is None:
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _snapshot_currency(snapshot_json: Optional[str]) -> Optional[str]:
        if not snapshot_json:
            return None
        try:
            payload = json.loads(snapshot_json)
        except (TypeError, ValueError):
            return None
        currency = payload.get("currency") if isinstance(payload, dict) else None
        return str(currency) if currency else None

    @staticmethod
    def _time_buckets(rows, storage_timezone: ZoneInfo, output_timezone: ZoneInfo):
        daily = defaultdict(lambda: {"requests": 0, "tokens": 0, "success": 0, "failed": 0})
        hourly = defaultdict(lambda: {"requests": 0, "tokens": 0})
        for request_time, total_tokens, status in rows:
            localized = request_time.replace(tzinfo=storage_timezone).astimezone(output_timezone)
            date_key = localized.strftime("%Y-%m-%d")
            hour_key = localized.strftime("%Y-%m-%d %H:00:00")
            daily[date_key]["requests"] += 1
            daily[date_key]["tokens"] += total_tokens or 0
            if status == "success":
                daily[date_key]["success"] += 1
            elif status == "failed":
                daily[date_key]["failed"] += 1
            hourly[hour_key]["requests"] += 1
            hourly[hour_key]["tokens"] += total_tokens or 0
        return daily, hourly

    def get_statistics(
        self,
        filters: Optional[Mapping[str, Any]] = None,
        timezone_name: Optional[str] = None,
    ) -> Dict:
        """获取可筛选、按请求时区分桶的统计信息。"""
        filters = dict(filters or {})
        storage_timezone = ZoneInfo(self.config.system.timezone)
        output_timezone = ZoneInfo(timezone_name or self.config.system.timezone)
        has_explicit_range = filters.get("start_time") is not None or filters.get("end_time") is not None
        now = datetime.now(storage_timezone).replace(tzinfo=None)

        with self.db_manager.get_session() as session:
            base_query = self._apply_statistics_filters(
                session.query(LLMRequestTrace),
                filters,
            )
            overview = base_query.with_entities(
                func.count(LLMRequestTrace.id),
                func.sum(case((LLMRequestTrace.status == "success", 1), else_=0)),  # type: ignore
                func.sum(case((LLMRequestTrace.status == "failed", 1), else_=0)),  # type: ignore
                func.sum(case((LLMRequestTrace.status == "pending", 1), else_=0)),  # type: ignore
                func.sum(LLMRequestTrace.total_tokens),
            ).one()

            # 成本必须按请求当时的价格快照累加，不能拿现价重算历史账单。
            cost_rows = base_query.with_entities(
                LLMRequestTrace.cost_snapshot_json
            ).all()
            total_cost = Decimal("0")
            unpriced_requests = 0
            cost_currency: Optional[str] = None
            for (snapshot_json,) in cost_rows:
                cost = self._snapshot_cost(snapshot_json)
                if cost is None:
                    unpriced_requests += 1
                    continue
                total_cost += cost
                if cost_currency is None:
                    cost_currency = self._snapshot_currency(snapshot_json)

            latency = base_query.with_entities(
                func.avg(LLMRequestTrace.ttft_ms),
                func.max(LLMRequestTrace.ttft_ms),
                func.avg(LLMRequestTrace.duration),
                func.avg(LLMRequestTrace.attempt_count),
            ).one()

            recent_query = base_query
            hourly_query = base_query
            if not has_explicit_range:
                recent_query = recent_query.filter(
                    LLMRequestTrace.request_time >= now - timedelta(days=30)  # type: ignore
                )
                hourly_query = hourly_query.filter(
                    LLMRequestTrace.request_time >= now - timedelta(hours=24)  # type: ignore
                )

            daily_rows = recent_query.with_entities(
                LLMRequestTrace.request_time,
                LLMRequestTrace.total_tokens,
                LLMRequestTrace.status,
            ).all()
            _, hourly_buckets = self._time_buckets(
                hourly_query.with_entities(
                    LLMRequestTrace.request_time,
                    LLMRequestTrace.total_tokens,
                    LLMRequestTrace.status,
                ).all(),
                storage_timezone,
                output_timezone,
            )
            daily_buckets, _ = self._time_buckets(
                daily_rows,
                storage_timezone,
                output_timezone,
            )

            return {
                "timezone": output_timezone.key,
                "overview": {
                    "total_requests": overview[0] or 0,
                    "success_requests": overview[1] or 0,
                    "failed_requests": overview[2] or 0,
                    "pending_requests": overview[3] or 0,
                    "total_tokens": overview[4] or 0,
                    "total_cost": str(total_cost),
                    "cost_currency": cost_currency,
                    "unpriced_requests": unpriced_requests,
                },
                "latency": {
                    # 非流式请求没有真实首字节，此处保持 None，不伪造 0。
                    "avg_ttft_ms": float(latency[0]) if latency[0] is not None else None,
                    "max_ttft_ms": int(latency[1]) if latency[1] is not None else None,
                    "avg_duration": float(latency[2]) if latency[2] is not None else None,
                    "avg_attempt_count": float(latency[3]) if latency[3] is not None else None,
                },
                "daily_stats": [
                    {"date": key, **daily_buckets[key]}
                    for key in sorted(daily_buckets)
                ],
                "hourly_stats": [
                    {"hour": key, **hourly_buckets[key]}
                    for key in sorted(hourly_buckets)
                ],
                "models": self._group_statistics(
                    recent_query,
                    LLMRequestTrace.model_id,
                    "model_id",
                ),
                "backends": self._group_statistics(
                    recent_query,
                    LLMRequestTrace.backend_name,
                    "backend_name",
                ),
                "providers": self._group_statistics(
                    recent_query,
                    LLMRequestTrace.provider,
                    "provider",
                ),
                "usage_sources": self._group_statistics(
                    recent_query,
                    LLMRequestTrace.usage_source,
                    "usage_source",
                ),
                # 失败分类已经建了索引，此前却没有任何聚合出口，
                # 于是「在失败什么」只能逐条翻请求日志。
                "error_categories": self._group_statistics(
                    recent_query.filter(LLMRequestTrace.error_category.isnot(None)),  # type: ignore
                    LLMRequestTrace.error_category,
                    "error_category",
                ),
            }
