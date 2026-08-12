import asyncio
from typing import Dict, List, Optional

from kirara_ai.database import DatabaseManager
from kirara_ai.events.event_bus import EventBus
from kirara_ai.events.tracing import TraceEvent
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.logger import get_logger
from kirara_ai.tracing.core import TracerBase, TraceRecord

logger = get_logger("TracingManager")

class TracingManager:
    """追踪管理器，负责管理所有类型的追踪器和协调追踪操作"""

    @Inject()
    def __init__(self, container: DependencyContainer, database_manager: DatabaseManager, event_bus: EventBus):
        self.container = container
        self.db_manager = database_manager
        self.event_bus = event_bus
        self.tracers: Dict[str, TracerBase] = {}
        self.logger = logger

    def initialize(self):
        """初始化追踪管理器"""
        self.logger.info("Initializing tracing manager")
        # 初始化所有注册的追踪器
        for name, tracer in self.tracers.items():
            try:
                tracer.initialize()
            except Exception as e:
                self.logger.error(f"Failed to initialize tracer {name}: {e}")
        self.logger.info("Tracing manager initialized")

    def shutdown(self):
        """关闭追踪管理器"""
        self.logger.info("Shutting down tracing manager")
        # 关闭所有追踪器
        for name, tracer in self.tracers.items():
            try:
                tracer.shutdown()
            except Exception as e:
                self.logger.error(f"Failed to shutdown tracer {name}: {e}")

    def register_tracer(self, name: str, tracer: TracerBase):
        """注册追踪器"""
        if name in self.tracers:
            raise ValueError(f"Tracer {name} already registered")
        self.tracers[name] = tracer

    def get_tracer(self, name: str) -> Optional[TracerBase]:
        """获取指定名称的追踪器"""
        return self.tracers.get(name)

    def get_all_tracers(self) -> Dict[str, TracerBase]:
        """获取所有追踪器"""
        return self.tracers.copy()

    def get_tracer_types(self) -> List[str]:
        """获取所有追踪器类型"""
        return list(self.tracers.keys())

    def publish_event(self, event: TraceEvent):
        """发布追踪事件"""
        self.event_bus.post(event)

    # WebSocket相关方法
    def register_ws_client(self, tracer_name: str) -> asyncio.Queue:
        """为指定追踪器注册WebSocket客户端"""
        if tracer := self.tracers.get(tracer_name):
            return tracer.register_ws_client()
        else:
            raise ValueError(f"Tracer {tracer_name} not found")
    def unregister_ws_client(self, tracer_name: str, queue: asyncio.Queue):
        """从指定追踪器注销WebSocket客户端"""
        if tracer := self.tracers.get(tracer_name):
            tracer.unregister_ws_client(queue)
        else:
            raise ValueError(f"Tracer {tracer_name} not found")

    # 通用追踪操作方法
    def get_recent_traces(self, tracer_name: str, limit: int = 100) -> List[TraceRecord]:
        """获取指定追踪器的最近追踪记录"""
        if tracer := self.get_tracer(tracer_name):
            return tracer.get_recent_traces(limit)
        else:
            raise ValueError(f"Tracer {tracer_name} not found")

    def get_trace_by_id(self, tracer_name: str, trace_id: str) -> Optional[TraceRecord]:
        """获取指定追踪器的特定追踪记录"""
        if tracer := self.get_tracer(tracer_name):
            return tracer.get_trace_by_id(trace_id)
        else:
            raise ValueError(f"Tracer {tracer_name} not found")
