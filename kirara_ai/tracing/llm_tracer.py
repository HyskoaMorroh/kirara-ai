from datetime import datetime, timedelta
from typing import Any, Dict

from sqlalchemy import case, func

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.events.tracing import LLMRequestCompleteEvent, LLMRequestFailEvent, LLMRequestStartEvent
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message
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
        request: LLMChatRequest
    ) -> str:
        """开始跟踪LLM请求"""
        trace_id = generate_trace_id()
        event = LLMRequestStartEvent(
            trace_id=trace_id,
            model_id=request.model or 'unknown',
            backend_name=backend_name,
            request=request.model_copy(deep=True)
        )
        # 存储活跃追踪信息
        self._active_traces[trace_id] = {
            'backend_name': backend_name,
            'start_time': event.start_time
        }
        # 发布事件
        self.event_bus.post(event)
        return trace_id

    def complete_request_tracking(
        self,
        trace_id: str,
        request: LLMChatRequest,
        response: LLMChatResponse
    ):
        """完成LLM请求跟踪"""
        if trace_id in self._active_traces:
            trace_data = self._active_traces[trace_id]
            model_id = request.model or trace_data.get('model_id', "unknown")
            backend_name = trace_data.get('backend_name', "unknown")
            start_time = trace_data.get('start_time', 0)

            event = LLMRequestCompleteEvent(
                trace_id=trace_id,
                model_id=model_id,
                backend_name=backend_name,
                request=request.model_copy(deep=True),
                response=response.model_copy(deep=True),
                start_time=start_time
            )
            # 移除活跃追踪
            del self._active_traces[trace_id]
            # 发布事件
            self.event_bus.post(event)

    def fail_request_tracking(
        self,
        trace_id: str,
        request: LLMChatRequest,
        error: Any
    ):
        """记录LLM请求失败"""
        if trace_id in self._active_traces:
            trace_data = self._active_traces[trace_id]
            model_id = request.model or trace_data.get('model_id', "unknown")
            backend_name = trace_data.get('backend_name', "unknown")
            start_time = trace_data.get('start_time', 0)

            event = LLMRequestFailEvent(
                trace_id=trace_id,
                model_id=model_id,
                backend_name=backend_name,
                request=request.model_copy(deep=True),
                error=error,
                start_time=start_time
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

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        with self.db_manager.get_session() as session:
            # 基础统计
            total_count = session.query(func.count(LLMRequestTrace.id)).scalar() or 0
            success_count = session.query(func.count(LLMRequestTrace.id)).filter_by(status="success").scalar() or 0
            failed_count = session.query(func.count(LLMRequestTrace.id)).filter_by(status="failed").scalar() or 0
            pending_count = session.query(func.count(LLMRequestTrace.id)).filter_by(status="pending").scalar() or 0
            total_tokens = session.query(func.sum(LLMRequestTrace.total_tokens)).scalar() or 0

            # 获取30天内的每日统计
            thirty_days_ago = datetime.now() - timedelta(days=30)
            daily_stats = session.query(
                func.strftime('%Y-%m-%d', LLMRequestTrace.request_time).label('date'),
                func.count(LLMRequestTrace.id).label('requests'),
                func.sum(LLMRequestTrace.total_tokens).label('tokens'),
                func.sum(case((LLMRequestTrace.status == 'success', 1), else_=0)).label('success'), # type: ignore
                func.sum(case((LLMRequestTrace.status == 'failed', 1), else_=0)).label('failed') # type: ignore
            ).filter(
                LLMRequestTrace.request_time >= thirty_days_ago # type: ignore
            ).group_by(
                func.strftime('%Y-%m-%d', LLMRequestTrace.request_time)
            ).order_by(
                func.strftime('%Y-%m-%d', LLMRequestTrace.request_time)
            ).all()

            daily_data = [{
                'date': str(row.date),
                'requests': row.requests,
                'tokens': row.tokens or 0,
                'success': row.success,
                'failed': row.failed
            } for row in daily_stats]

            # 按模型分组统计（最近30天）
            model_stats = []
            model_counts = session.query(
                LLMRequestTrace.model_id, # type: ignore
                func.count(LLMRequestTrace.id).label('count'),
                func.sum(LLMRequestTrace.total_tokens).label('tokens'),
                func.avg(LLMRequestTrace.duration).label('avg_duration')
            ).filter( # type: ignore
                LLMRequestTrace.request_time >= thirty_days_ago # type: ignore
            ).group_by(
                LLMRequestTrace.model_id
            ).all()

            for model_id, count, tokens, avg_duration in model_counts:
                model_stats.append({
                    'model_id': model_id,
                    'count': count,
                    'tokens': tokens or 0,
                    'avg_duration': float(avg_duration) if avg_duration else 0
                })

            # 按后端分组统计（最近30天）
            backend_stats = []
            backend_counts = session.query(
                LLMRequestTrace.backend_name, # type: ignore
                func.count(LLMRequestTrace.id).label('count'),
                func.sum(LLMRequestTrace.total_tokens).label('tokens'),
                func.avg(LLMRequestTrace.duration).label('avg_duration')
            ).filter( # type: ignore
                LLMRequestTrace.request_time >= thirty_days_ago # type: ignore
            ).group_by(
                LLMRequestTrace.backend_name
            ).all()

            for backend_name, count, tokens, avg_duration in backend_counts:
                backend_stats.append({
                    'backend_name': backend_name,
                    'count': count,
                    'tokens': tokens or 0,
                    'avg_duration': float(avg_duration) if avg_duration else 0
                })

            # 获取每小时统计（最近24小时）
            one_day_ago = datetime.now() - timedelta(hours=24)
            hourly_stats = session.query(
                func.strftime('%Y-%m-%d %H:00:00', LLMRequestTrace.request_time).label('hour'),
                func.count(LLMRequestTrace.id).label('requests'),
                func.sum(LLMRequestTrace.total_tokens).label('tokens')
            ).filter(
                LLMRequestTrace.request_time >= one_day_ago # type: ignore
            ).group_by(
                func.strftime('%Y-%m-%d %H:00:00', LLMRequestTrace.request_time)
            ).order_by(
                func.strftime('%Y-%m-%d %H:00:00', LLMRequestTrace.request_time)
            ).all()

            hourly_data = [{
                'hour': str(row.hour),
                'requests': row.requests,
                'tokens': row.tokens or 0
            } for row in hourly_stats]

            return {
                'overview': {
                    'total_requests': total_count,
                    'success_requests': success_count,
                    'failed_requests': failed_count,
                    'pending_requests': pending_count,
                    'total_tokens': total_tokens,
                },
                'daily_stats': daily_data,
                'hourly_stats': hourly_data,
                'models': model_stats,
                'backends': backend_stats
            }
