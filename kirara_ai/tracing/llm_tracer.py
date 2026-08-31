from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Dict, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, case, func

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

#: 趋势分桶在 SQL 侧的槽长（分钟）。
#:
#: 15 分钟是所有 IANA 时区偏移的公约数（含印度 +05:30、尼泊尔 +05:45），
#: 因此一个槽只会落进一个本地小时——按整小时分槽会把这些时区的一个槽
#: 劈到两个本地小时里，趋势图上表现为两根都偏低的柱子。
_SLOT_MINUTES = 15

#: 一天有多少个槽。
_SLOTS_PER_DAY = 24 * 60 // _SLOT_MINUTES

#: 槽序号的零点。与 `_JULIAN_EPOCH` 必须指向同一时刻。
_SLOT_EPOCH = datetime(1970, 1, 1)

#: 1970-01-01T00:00:00 的儒略日。SQLite 的 `julianday()` 返回儒略日，
#: 减掉这个常量再乘每天的槽数即得槽序号。
_JULIAN_EPOCH = 2440587.5

#: 槽序号转整型时用的 SQL 类型。
_SLOT_CAST_TYPE = Integer

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
            elif field.endswith("__is_null"):
                # 「未标注」维度：显式筛选该列为 NULL 的记录。用一个独立后缀而不是
                # 空串，因为空串在参数解析层被当成「没填」丢掉——那会让
                # 「只看未标注」静默变成「看全部」。
                column_name = field.removesuffix("__is_null")
                column = getattr(LLMRequestTrace, column_name, None)
                if column is not None:
                    query = query.filter(column.is_(None))
            elif hasattr(LLMRequestTrace, field):
                query = query.filter(getattr(LLMRequestTrace, field) == value)
        return query

    @staticmethod
    def _group_statistics(query, column, key: str) -> list[Dict[str, Any]]:
        """按维度聚合请求数、Token、平均耗时与成本。

        成本沿用请求当时的定价快照（不能拿现价重算历史账单），但快照里的总成本
        在写入时已投影到 ``total_cost`` 列，因此这里整段由 SQL 完成。
        此前是把该维度所需的原始列全部取回 Python 再聚合——数据量一大，
        统计页的每一个分组都要物化一次结果集，索引帮不上忙。
        排序仍保持「按请求数降序、维度键升序」。
        """
        rows = query.with_entities(
            column,
            func.count(LLMRequestTrace.id),
            func.sum(LLMRequestTrace.total_tokens),
            func.avg(LLMRequestTrace.duration),
            func.sum(LLMRequestTrace.total_cost),
            # 未定价条数 = 总数 - 有成本的条数。用 SUM(CASE ...) 而不是
            # COUNT(total_cost)，因为后者在不同数据库对 NULL 的处理上不完全一致。
            func.sum(case((LLMRequestTrace.total_cost.is_(None), 1), else_=0)),  # type: ignore
            # 分组也要给四类拆分：同样的 100 总量，一家几乎全是输入、
            # 另一家几乎全是输出，处置完全不同（前者查上下文与历史长度，
            # 后者查 prompt 与 max_tokens）。只给一个 `tokens` 就把该查什么
            # 留给读者猜。
            func.sum(LLMRequestTrace.prompt_tokens),
            func.sum(LLMRequestTrace.completion_tokens),
            func.sum(LLMRequestTrace.cached_tokens),
            func.sum(LLMRequestTrace.cache_write_tokens),
            func.count(LLMRequestTrace.cached_tokens),
            func.count(LLMRequestTrace.cache_write_tokens),
            # 成功率所需的三态计数。
            #
            # `error_categories` 回答「在失败什么」，回答不了「谁在失败」——
            # 一个 timeout 分组里可能混着三家供应商。而故障转移队列该把谁排后面，
            # 依据正是各家的成功率；没有它只能翻请求日志人工计数。
            #
            # `pending` 单列且不进分母：还在跑的请求既不是成功也不是失败，
            # 把它算作失败会让正在进行的长请求把成功率压下去。
            func.sum(case((LLMRequestTrace.status == "success", 1), else_=0)),  # type: ignore
            func.sum(case((LLMRequestTrace.status == "failed", 1), else_=0)),  # type: ignore
            func.sum(case((LLMRequestTrace.status == "pending", 1), else_=0)),  # type: ignore
        ).group_by(column).all()

        results = []
        for (
            value,
            counted,
            tokens,
            avg_duration,
            summed_cost,
            unpriced,
            prompt_tokens,
            completion_tokens,
            cached_tokens,
            cache_write_tokens,
            cached_reported,
            cache_write_reported,
            success_requests,
            failed_requests,
            pending_requests,
        ) in rows:
            succeeded = int(success_requests or 0)
            failed = int(failed_requests or 0)
            concluded = succeeded + failed
            results.append(
                {
                    key: value,
                    "count": int(counted or 0),
                    "tokens": int(tokens or 0),
                    "avg_duration": float(avg_duration) if avg_duration is not None else 0.0,
                    "cost": str(Decimal(str(summed_cost or 0))),
                    "unpriced_requests": int(unpriced or 0),
                    "prompt_tokens": int(prompt_tokens or 0),
                    "completion_tokens": int(completion_tokens or 0),
                    # `None` 表示这一组里没有任何上游报过缓存，与「报了 0」不同。
                    "cached_tokens": (
                        int(cached_tokens or 0) if int(cached_reported or 0) > 0 else None
                    ),
                    "cache_write_tokens": (
                        int(cache_write_tokens or 0)
                        if int(cache_write_reported or 0) > 0
                        else None
                    ),
                    "success_requests": succeeded,
                    "failed_requests": failed,
                    "pending_requests": int(pending_requests or 0),
                    # 一条都没跑完时是「未知」而不是 0%：报 0% 会让一家刚配好、
                    # 只有一条在途请求的供应商看起来是最差的那一个。
                    "success_rate": (succeeded / concluded) if concluded else None,
                }
            )
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
    def _fetch_bucket_rows(query, columns, storage_timezone: ZoneInfo):
        """Aggregate trend rows in SQL, one row per 15-minute slot per currency.

        为什么是「15 分钟槽」而不是直接 `GROUP BY date(request_time)`：

        - **数据库不做时区截断。** SQLite 没有时区库，`date()` 只能按存储值截断；
          按存储时区分完桶再搬到输出时区，跨时区对账时日界会整体错位一整天，
          而那种错误不会报错，只会给出一个看起来正常的数字。
        - **15 分钟能整除所有真实时区偏移。** 印度 +05:30、尼泊尔 +05:45 这类
          半小时/三刻钟偏移下，按整小时分槽会把一个槽劈到两个本地小时里。
          15 分钟是所有 IANA 偏移的公约数，因此槽到本地桶是一对一的映射。
        - **取回行数由时间跨度决定，不再由请求数决定。** 这正是需求 22.2 结尾
          点名的那一点：一年的范围是固定的槽数量级，而不是一年的请求数。

        `GROUP BY` 的键额外带上 ``status`` 与 ``cost_currency``：成功/失败要分开
        计数，而两种货币加进同一个数字得到的是一串没有单位的数字。
        """
        (
            request_time_column,
            total_tokens,
            status,
            prompt_tokens,
            completion_tokens,
            cached_tokens,
            cache_write_tokens,
            total_cost,
            cost_currency,
        ) = columns
        # 槽序号 = 距 epoch 的 15 分钟数。用整数除法而不是字符串截断：
        # 字符串在不同方言上的函数名不一致，而整数运算到处都一样。
        slot = func.cast(
            (func.julianday(request_time_column) - _JULIAN_EPOCH)
            * _SLOTS_PER_DAY,
            _SLOT_CAST_TYPE,
        )
        rows = (
            query.with_entities(
                slot.label("slot"),
                status,
                cost_currency,
                func.count().label("requests"),
                func.sum(total_tokens),
                func.sum(prompt_tokens),
                func.sum(completion_tokens),
                func.sum(cached_tokens),
                func.sum(cache_write_tokens),
                func.sum(total_cost),
                func.sum(case((total_cost.is_(None), 1), else_=0)),  # type: ignore
            )
            .group_by(slot, status, cost_currency)
            .all()
        )
        return rows

    @staticmethod
    def _time_buckets(rows, storage_timezone: ZoneInfo, output_timezone: ZoneInfo):
        """按本地日期与小时分桶，带四类 Token 拆分与成本。

        输入是 `_fetch_bucket_rows` 的**已聚合**行（每 15 分钟槽 × 状态 × 币种
        一行），不是原始请求行。时区换算仍在 Python 里用真正的 `astimezone`
        完成——只有它认得 DST 与半小时偏移。

        只给一个 `tokens` 的趋势线看得出「涨了」，看不出涨的是输入还是输出——
        而两者的处置相反（输入涨查上下文与历史长度，输出涨查 prompt 与
        max_tokens）。`tokens` 键保持原义不动，前端既有图表继续读它。

        成本进分桶是为了回答「这个月贵了三倍，是哪天开始的」。只有一个 30 天
        合计时，这个问题只能靠手工二分时间范围反复重查，而账单异常恰恰最需要
        快速定位到某一天（换了模型、上了新流量、缓存失效）。

        缓存两列在分桶里按 0 累加而不是 `None`：趋势是逐时间累加的折线，
        中间出现 `None` 会把线断开，而「这个小时没有上游报缓存」在趋势图上
        与「报了 0」并无不同处置。需要区分未知与零的是 `overview.cache_hit_rate`，
        那里保留了 `None`。
        """
        def _empty_daily():
            return {
                "requests": 0,
                "tokens": 0,
                "success": 0,
                "failed": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "unpriced_requests": 0,
            }

        def _empty_hourly():
            return {
                "requests": 0,
                "tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "unpriced_requests": 0,
            }

        daily = defaultdict(_empty_daily)
        hourly = defaultdict(_empty_hourly)
        # 币种分开累计，最后再投影成 `cost` / `cost_currency` / `cost_by_currency`。
        # 与 `overview` 同一口径：两种货币加进同一个数字得到的是一个没有单位的数，
        # 而那不会报错。
        daily_costs: dict[str, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(Decimal)
        )
        hourly_costs: dict[str, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(Decimal)
        )
        for row in rows:
            (
                slot,
                status,
                cost_currency,
                requests,
                total_tokens,
                prompt_tokens,
                completion_tokens,
                cached_tokens,
                cache_write_tokens,
                total_cost,
                unpriced,
            ) = row
            # 槽序号还原成槽起点，再做一次真正的时区换算。槽长 15 分钟，
            # 能整除所有 IANA 偏移，因此一个槽只会落进一个本地小时。
            slot_start = _SLOT_EPOCH + timedelta(minutes=int(slot) * _SLOT_MINUTES)
            localized = slot_start.replace(tzinfo=storage_timezone).astimezone(
                output_timezone
            )
            date_key = localized.strftime("%Y-%m-%d")
            hour_key = localized.strftime("%Y-%m-%d %H:00:00")
            requests = int(requests or 0)
            daily[date_key]["requests"] += requests
            daily[date_key]["tokens"] += int(total_tokens or 0)
            daily[date_key]["prompt_tokens"] += int(prompt_tokens or 0)
            daily[date_key]["completion_tokens"] += int(completion_tokens or 0)
            daily[date_key]["cached_tokens"] += int(cached_tokens or 0)
            daily[date_key]["cache_write_tokens"] += int(cache_write_tokens or 0)
            if status == "success":
                daily[date_key]["success"] += requests
            elif status == "failed":
                daily[date_key]["failed"] += requests
            hourly[hour_key]["requests"] += requests
            hourly[hour_key]["tokens"] += int(total_tokens or 0)
            hourly[hour_key]["prompt_tokens"] += int(prompt_tokens or 0)
            hourly[hour_key]["completion_tokens"] += int(completion_tokens or 0)
            hourly[hour_key]["cached_tokens"] += int(cached_tokens or 0)
            hourly[hour_key]["cache_write_tokens"] += int(cache_write_tokens or 0)
            # 未定价条数单列。按 0 元并入当天合计，会把「有请求没匹配到
            # 价格版本」显示成「这天便宜」——两个完全不同的结论。
            unpriced = int(unpriced or 0)
            daily[date_key]["unpriced_requests"] += unpriced
            hourly[hour_key]["unpriced_requests"] += unpriced
            if total_cost is not None:
                amount = Decimal(str(total_cost))
                currency = str(cost_currency or "")
                daily_costs[date_key][currency] += amount
                hourly_costs[hour_key][currency] += amount

        def _project_costs(bucket: dict, by_currency: dict[str, Decimal]) -> None:
            """把按币种的累计投影成 `cost` / `cost_currency` / `cost_by_currency`。

            主币种取金额最大者，与 `overview` 完全同一规则；其余币种逐一列出而不是
            加进同一个数字。没有任何定价证据时 `cost` 是 `"0"`、币种是 `None`——
            不编一个币种出来。
            """
            bucket["cost_by_currency"] = {
                key: str(value) for key, value in by_currency.items()
            }
            if not by_currency:
                bucket["cost"] = "0"
                bucket["cost_currency"] = None
                return
            primary, amount = max(by_currency.items(), key=lambda item: item[1])
            bucket["cost"] = str(amount)
            bucket["cost_currency"] = primary or None

        for key, bucket in daily.items():
            _project_costs(bucket, daily_costs.get(key, {}))
        for key, bucket in hourly.items():
            _project_costs(bucket, hourly_costs.get(key, {}))
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
                # 需求 22.1 点名「输入/输出/缓存 Token」。四列每行都记着，
                # 但此前聚合只 SUM 了 total_tokens，把四类合成一个数——
                # 于是缓存命中率算不出来，而输入 Token 通常是缓存读取的 5~10 倍：
                # 「总量没变、命中率从 80% 掉到 0%」时账单会翻几倍，
                # 而统计页在这两种情况下显示的数字完全一样。
                func.sum(LLMRequestTrace.prompt_tokens),
                func.sum(LLMRequestTrace.completion_tokens),
                func.sum(LLMRequestTrace.cached_tokens),
                func.sum(LLMRequestTrace.cache_write_tokens),
                # COUNT 只数非 NULL：用来区分「上游没报缓存」与「报了、是 0」。
                # SUM 对两者都给 0（或 NULL），单靠它分不开，而这两件事的处置
                # 相反——前者要去查上游是否返回 usage，后者才是真的没命中。
                func.count(LLMRequestTrace.cached_tokens),
                func.count(LLMRequestTrace.cache_write_tokens),
            ).one()
            total_prompt_tokens = int(overview[5] or 0)
            total_completion_tokens = int(overview[6] or 0)
            cache_read_reported = int(overview[9] or 0) > 0
            cache_write_reported = int(overview[10] or 0) > 0
            total_cached_tokens = int(overview[7] or 0) if cache_read_reported else None
            total_cache_write_tokens = (
                int(overview[8] or 0) if cache_write_reported else None
            )
            # 命中率 = 缓存读取 /（输入 + 缓存写入 + 缓存读取），与上游计价口径一致：
            # 三者相加才是这次请求真正付费的输入侧总量。
            #
            # 没有任何上游报过缓存时是 `None`（未知）而不是 0%——报 0% 会让运维
            # 去查一个并不存在的缓存失效问题。分母为 0 时同理。
            cache_hit_rate: Optional[float] = None
            if cache_read_reported or cache_write_reported:
                denominator = (
                    total_prompt_tokens
                    + int(overview[8] or 0)
                    + int(overview[7] or 0)
                )
                if denominator > 0:
                    cache_hit_rate = int(overview[7] or 0) / denominator

            # 成本必须按请求当时的价格快照累加，不能拿现价重算历史账单。
            # 快照里的总成本在写入时已投影到 `total_cost` / `cost_currency`
            # 两列（见 `LLMRequestTrace.apply_cost_projection`），因此这里用
            # SUM 完成，而不是把筛选后的每一行都取回来逐条解析 JSON——
            # 后者会让统计页在数据量大时变成一次全表物化，索引完全帮不上忙。
            #
            # 按币种分组求和：把两种货币直接相加是错的，哪怕只有一种也要
            # 走同一条路径，否则「什么时候会分组」取决于数据碰巧长什么样。
            cost_rows = (
                base_query.with_entities(
                    LLMRequestTrace.cost_currency,
                    func.sum(LLMRequestTrace.total_cost),
                    func.count(LLMRequestTrace.id),
                )
                .filter(LLMRequestTrace.total_cost.isnot(None))
                .group_by(LLMRequestTrace.cost_currency)
                .all()
            )
            priced_requests = 0
            total_cost = Decimal("0")
            cost_currency: Optional[str] = None
            # 币种按金额降序取主币种：一份混币账单里「总额」只能有一个单位，
            # 取金额最大的那个并保留其余在 `cost_by_currency` 里，
            # 而不是让它取决于行的物理顺序。
            for currency, summed, counted in sorted(
                cost_rows,
                key=lambda row: Decimal(str(row[1] or 0)),
                reverse=True,
            ):
                priced_requests += int(counted or 0)
                amount = Decimal(str(summed or 0))
                if cost_currency is None:
                    cost_currency = currency
                    total_cost = amount
            cost_by_currency = {
                str(currency or ""): str(Decimal(str(summed or 0)))
                for currency, summed, _ in cost_rows
            }
            unpriced_requests = max(0, int(overview[0] or 0) - priced_requests)

            latency = base_query.with_entities(
                func.avg(LLMRequestTrace.ttft_ms),
                func.max(LLMRequestTrace.ttft_ms),
                func.avg(LLMRequestTrace.duration),
                func.avg(LLMRequestTrace.attempt_count),
                # 重试与故障转移分开统计：平均 3 次尝试可能是「一家重试两次」
                # 也可能是「换了两家」，两者的处置相反（调超时 vs 查供应商健康）。
                func.avg(LLMRequestTrace.retry_count),
                func.avg(LLMRequestTrace.failover_count),
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

            # 四类 Token 一并取回：分桶要能回答「涨的是输入还是输出」。
            # 聚合在 SQL 侧完成（见 `_fetch_bucket_rows`），取回的行数由时间跨度
            # 决定而不是请求数——此前是把区间内每一行的这十列读进进程内存，
            # 显式时间范围会跳过近 30 天/近 24 小时的兜底过滤器，于是
            # 「导出全年趋势」等于一次全区间物化。
            bucket_columns = (
                LLMRequestTrace.request_time,
                LLMRequestTrace.total_tokens,
                LLMRequestTrace.status,
                LLMRequestTrace.prompt_tokens,
                LLMRequestTrace.completion_tokens,
                LLMRequestTrace.cached_tokens,
                LLMRequestTrace.cache_write_tokens,
                # 成本进趋势：回答「贵了三倍是哪天开始的」。取的是写入时冻结的
                # 快照投影列，不是现价——历史账单不能被后来的改价改写。
                LLMRequestTrace.total_cost,
                LLMRequestTrace.cost_currency,
            )
            _, hourly_buckets = self._time_buckets(
                self._fetch_bucket_rows(hourly_query, bucket_columns, storage_timezone),
                storage_timezone,
                output_timezone,
            )
            daily_buckets, _ = self._time_buckets(
                self._fetch_bucket_rows(recent_query, bucket_columns, storage_timezone),
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
                    # 四类拆分与总数并列给出，总数的含义不变——否则历史看板
                    # 会在同一个字段上前后不一致。
                    "total_prompt_tokens": total_prompt_tokens,
                    "total_completion_tokens": total_completion_tokens,
                    # `None` = 没有上游报过缓存（未知）；`0` = 报了、确实没命中。
                    "total_cached_tokens": total_cached_tokens,
                    "total_cache_write_tokens": total_cache_write_tokens,
                    "cache_hit_rate": cache_hit_rate,
                    "total_cost": str(total_cost),
                    "cost_currency": cost_currency,
                    # 混币账单里「总额」只能有一个单位，其余币种在这里逐一列出，
                    # 而不是被悄悄加进同一个数字。单币种部署下只有一个键。
                    "cost_by_currency": cost_by_currency,
                    "unpriced_requests": unpriced_requests,
                },
                "latency": {
                    # 非流式请求没有真实首字节，此处保持 None，不伪造 0。
                    "avg_ttft_ms": float(latency[0]) if latency[0] is not None else None,
                    "max_ttft_ms": int(latency[1]) if latency[1] is not None else None,
                    "avg_duration": float(latency[2]) if latency[2] is not None else None,
                    "avg_attempt_count": float(latency[3]) if latency[3] is not None else None,
                    # 两者分别给出；`None` 表示这批请求里没有 attempt 数据，
                    # 与 0（确实一次成功）不同。
                    "avg_retry_count": float(latency[4]) if latency[4] is not None else None,
                    "avg_failover_count": float(latency[5]) if latency[5] is not None else None,
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
