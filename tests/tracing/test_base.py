import unittest
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import Column, Integer

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.database import DatabaseManager
from kirara_ai.database.manager import Base
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message, Usage
from kirara_ai.tracing.core import TraceRecord
from kirara_ai.tracing.models import LLMRequestTrace


class TestTraceRecord(TraceRecord):
    """用于测试的追踪记录类"""
    __test__ = False
    __tablename__ = "test_traces"
    __table_args__ = {'extend_existing': True}
    
    id = Column(Integer, primary_key=True)

    def update_from_event(self, event):
        pass

    def to_dict(self) -> Dict[str, Any]:
        return {}

    def to_detail_dict(self) -> Dict[str, Any]:
        return {}


class TracingTestBase(unittest.TestCase):
    """追踪系统测试基类"""

    def setUp(self):
        """测试前的准备工作"""
        self.container = DependencyContainer()
        self.container.register(DependencyContainer, self.container)
        self.event_bus = EventBus()
        self.container.register(EventBus, self.event_bus)
        self.container.register(GlobalConfig, GlobalConfig())
        
        # 使用内存数据库进行测试
        self.db_manager = DatabaseManager(self.container, database_url="sqlite:///:memory:", is_debug=True)
        self.db_manager.initialize()
        Base.metadata.create_all(self.db_manager.engine)
        self.container.register(DatabaseManager, self.db_manager)

    def tearDown(self):
        """测试后的清理工作"""
        self.db_manager.shutdown()

    def create_test_request(self, model: str = "test-model") -> LLMChatRequest:
        """创建测试用的LLM请求"""
        return LLMChatRequest(
            model=model,
            messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="test message")])]
        )

    def create_test_response(self, usage: Optional[Usage] = None) -> LLMChatResponse:
        """创建测试用的LLM响应"""
        if usage is None:
            usage = Usage(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30
            )
        return LLMChatResponse(
            model="test-model",
            message=Message(role="assistant", content=[LLMChatTextContent(text="test response")]),
            usage=usage
        )

    def create_test_trace(self) -> LLMRequestTrace:
        """创建测试用的追踪记录"""
        trace = LLMRequestTrace()
        trace.trace_id = "test-trace-id"
        trace.model_id = "test-model"
        trace.backend_name = "test-backend"
        trace.request_time = datetime.now()
        return trace 