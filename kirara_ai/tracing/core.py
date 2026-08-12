import abc
import asyncio
import uuid
from asyncio import Queue
from typing import Any, Dict, Generic, List, Optional, Tuple, Type, TypeVar

from sqlalchemy import Column, DateTime, String, asc

from kirara_ai.database import Base, DatabaseManager
from kirara_ai.events.event_bus import EventBus
from kirara_ai.events.tracing import TraceEvent
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.logger import get_logger

logger = get_logger("Tracking")


class TraceRecord(Base):
    """跟踪记录基类，用于ORM映射"""

    __abstract__ = True

    trace_id = Column(String(64), nullable=False, index=True, unique=True)
    status = Column(String(20), nullable=False, default="pending")
    
    request_time = Column(DateTime, nullable=False, index=True)

    @abc.abstractmethod
    def update_from_event(self, event: TraceEvent) -> None:
        """从事件更新记录"""

    @abc.abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """将记录转换为字典，用于JSON序列化"""

    @abc.abstractmethod
    def to_detail_dict(self) -> Dict[str, Any]:
        """将详细记录转换为字典，用于JSON序列化"""


def generate_trace_id() -> str:
    """生成唯一的追踪ID"""
    return str(uuid.uuid4())

# 定义泛型类型变量，用于追踪事件、追踪器和追踪记录
T = TypeVar('T')
E = TypeVar('E', bound=TraceEvent)  # 事件类型
R = TypeVar('R', bound=TraceRecord)  # 记录类型

class TracerBase(Generic[R], abc.ABC):
    """追踪器基类"""

    # 追踪器名称，用于区分不同类型的追踪器
    name: str
    record_class: Type[R]

    @Inject()
    def __init__(self, container: DependencyContainer, record_class: Type[R], db_manager: DatabaseManager, event_bus: EventBus):
        self.record_class = record_class
        self.container = container
        self.db_manager = db_manager
        self.event_bus = event_bus
        self.logger = logger

        # 活跃追踪的映射表
        self._active_traces: Dict[str, Dict[str, Any]] = {}

        # WebSocket消息队列映射表
        self._ws_queues: List[Queue] = []

    def initialize(self):
        """初始化追踪器，注册事件处理程序"""
        self.logger.info(f"Initializing {self.name} tracer")
        self._register_event_handlers()
        self.logger.info(f"{self.name} tracer initialized")

    def shutdown(self):
        """关闭追踪器，取消事件注册"""
        self.logger.info(f"Shutting down {self.name} tracer")
        self._unregister_event_handlers()

        # 关闭所有WebSocket连接
        for queue in list(self._ws_queues):
            try:
                queue.put_nowait(None)
            except Exception:
                pass
        self._ws_queues.clear()

    @abc.abstractmethod
    def _register_event_handlers(self):
        """注册事件处理程序"""

    @abc.abstractmethod
    def _unregister_event_handlers(self):
        """取消事件处理程序注册"""

    def get_traces(
        self,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "request_time",
        order_desc: bool = True
    ) -> Tuple[List[R], int]:
        """统一的追踪记录查询方法
        
        Args:
            filters: 过滤条件字典
            page: 页码（从1开始）
            page_size: 每页记录数
            order_by: 排序字段
            order_desc: 是否降序排序
            
        Returns:
            Tuple[List[R], int]: 记录列表和总记录数
        """
        with self.db_manager.get_session() as session:
            from sqlalchemy import desc, func, select

            # 构建基础查询
            query = select(self.record_class)
            count_query = select(func.count()).select_from(self.record_class)
            
            # 应用过滤条件
            if filters:
                for field, value in filters.items():
                    if value is not None and hasattr(self.record_class, field):
                        query = query.filter(getattr(self.record_class, field) == value)
                        count_query = count_query.filter(getattr(self.record_class, field) == value)
            
            # 应用排序
            if hasattr(self.record_class, order_by):
                order_func = desc if order_desc else asc
                query = query.order_by(order_func(getattr(self.record_class, order_by)))
            
            # 应用分页
            if page > 0 and page_size > 0:
                query = query.offset((page - 1) * page_size).limit(page_size)
            
            # 执行查询
            total = session.execute(count_query).scalar() or 0
            records = list(session.execute(query).scalars().all())
            
            return records, total

    def get_recent_traces(self, limit: int = 100) -> List[R]:
        """获取最近的跟踪记录"""
        with self.db_manager.get_session() as session:
            from sqlalchemy import desc, select
            stmt = select(self.record_class).order_by(desc(self.record_class.request_time)).limit(limit)
            result = session.execute(stmt)
            return list(result.scalars().all())

    def get_trace_by_id(self, trace_id: str) -> Optional[R]:
        """根据追踪ID获取跟踪记录"""
        with self.db_manager.get_session() as session:
            return session.query(self.record_class).filter_by(trace_id=trace_id).first()

    def save_trace_record(self, record: R) -> Dict[str, Any]:
        """保存追踪记录到数据库"""
        with self.db_manager.get_session() as session:
            session.add(record)
            session.commit()
            return record.to_dict()

    def update_trace_record(self, trace_id: str, event: TraceEvent) -> Optional[Dict[str, Any]]:
        """更新追踪记录"""
        with self.db_manager.get_session() as session:
            if (
                record := session.query(self.record_class)
                .filter_by(trace_id=trace_id)
                .first()
            ):
                record.update_from_event(event)
                session.commit()
                return record.to_dict()
            return None

    # WebSocket相关方法
    def register_ws_client(self) -> Queue:
        """注册WebSocket客户端，返回一个消息队列"""
        queue: Queue = Queue()
        self._ws_queues.append(queue)
        return queue

    def unregister_ws_client(self, queue: Queue):
        """注销WebSocket客户端"""
        if queue in self._ws_queues:
            self._ws_queues.remove(queue)

    def broadcast_ws_message(self, message: Dict):
        """向所有WebSocket客户端广播消息"""
        dead_queues = []
        for queue in self._ws_queues:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self.logger.warning(f"Queue is full, message dropped")
                dead_queues.append(queue)
            except Exception as e:
                self.logger.error(f"Error broadcasting message: {e}")
                dead_queues.append(queue)
        
        # 清理失效的队列
        for queue in dead_queues:
            if queue in self._ws_queues:
                self._ws_queues.remove(queue)
