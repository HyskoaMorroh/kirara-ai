from datetime import datetime
from unittest import IsolatedAsyncioTestCase

from kirara_ai.events.tracing import TraceEvent
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.tracing.core import TracerBase, generate_trace_id
from tests.tracing.test_base import TestTraceRecord, TracingTestBase


class TestEvent(TraceEvent):
    """用于测试的事件类"""
    __test__ = False
    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self.start_time = datetime.now().timestamp()


class TestTracer(TracerBase[TestTraceRecord]):
    """用于测试的追踪器"""
    __test__ = False
    name = "test"
    record_class = TestTraceRecord
    @Inject()
    def __init__(self, container: DependencyContainer):
        super().__init__(container, record_class=TestTraceRecord)

    def _register_event_handlers(self):
        self.event_bus.register(TestEvent, self._on_test_event)

    def _unregister_event_handlers(self):
        self.event_bus.unregister(TestEvent, self._on_test_event)

    def _on_test_event(self, event: TestEvent):
        """处理测试事件"""
        trace = TestTraceRecord()
        trace.trace_id = event.trace_id
        trace.request_time = datetime.now()
        self.save_trace_record(trace)


class TestTracerBase(TracingTestBase, IsolatedAsyncioTestCase):
    """追踪器基类测试"""

    def setUp(self):
        super().setUp()
        self.tracer = TestTracer(self.container)
        self.tracer.initialize()

    def tearDown(self):
        self.tracer.shutdown()
        super().tearDown()

    def test_generate_trace_id(self):
        """测试生成追踪ID"""
        trace_id1 = generate_trace_id()
        trace_id2 = generate_trace_id()

        self.assertIsNotNone(trace_id1)
        self.assertIsNotNone(trace_id2)
        self.assertNotEqual(trace_id1, trace_id2)

    def test_get_traces(self):
        """测试获取追踪记录"""
        # 创建一些测试数据
        for i in range(5):
            event = TestEvent(f"test-trace-{i}")
            self.event_bus.post(event)

        # 测试基本查询
        traces, total = self.tracer.get_traces()
        self.assertEqual(total, 5)
        self.assertEqual(len(traces), 5)

        # 测试分页
        traces, total = self.tracer.get_traces(page=1, page_size=2)
        self.assertEqual(total, 5)
        self.assertEqual(len(traces), 2)

        # 测试过滤
        traces, total = self.tracer.get_traces(filters={"trace_id": "test-trace-0"})
        self.assertEqual(total, 1)
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].trace_id, "test-trace-0")

    def test_get_recent_traces(self):
        """测试获取最近的追踪记录"""
        # 创建一些测试数据
        for i in range(5):
            event = TestEvent(f"test-trace-{i}")
            self.event_bus.post(event)

        # 测试限制数量
        traces = self.tracer.get_recent_traces(limit=3)
        self.assertEqual(len(traces), 3)

    def test_get_trace_by_id(self):
        """测试根据ID获取追踪记录"""
        event = TestEvent("test-trace-id")
        self.event_bus.post(event)

        # 测试获取存在的记录
        trace = self.tracer.get_trace_by_id("test-trace-id")
        self.assertIsNotNone(trace)
        self.assertEqual(trace.trace_id, "test-trace-id")

        # 测试获取不存在的记录
        trace = self.tracer.get_trace_by_id("non-existent-id")
        self.assertIsNone(trace)

    async def test_websocket_operations(self):
        """测试WebSocket相关操作"""
        # 创建一个测试队列
        queue = self.tracer.register_ws_client()

        # 广播一条消息
        test_message = {"type": "test", "data": "test data"}
        self.tracer.broadcast_ws_message(test_message)

        # 验证消息是否被正确广播
        message = await queue.get()
        self.assertEqual(message, test_message)

        # 注销客户端
        self.tracer.unregister_ws_client(queue)

        # 验证客户端是否被正确注销
        self.assertNotIn(queue, self.tracer._ws_queues)

    def test_save_and_update_trace_record(self):
        """测试保存和更新追踪记录"""
        # 创建并保存记录
        trace = TestTraceRecord()
        trace.trace_id = "test-trace-id"
        trace.request_time = datetime.now()
        saved_trace = self.tracer.save_trace_record(trace)
        self.assertIsNotNone(saved_trace)

        # 更新记录
        event = TestEvent("test-trace-id")
        updated_trace = self.tracer.update_trace_record("test-trace-id", event)
        self.assertIsNotNone(updated_trace)

        # 测试更新不存在的记录
        non_existent = self.tracer.update_trace_record("non-existent-id", event)
        self.assertIsNone(non_existent) 