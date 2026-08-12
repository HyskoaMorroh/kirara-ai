import asyncio
from unittest import IsolatedAsyncioTestCase

from kirara_ai.tracing import LLMTracer, TracingManager
from tests.tracing.test_base import TracingTestBase
from tests.tracing.test_core import TestTracer


class TestTracingManager(TracingTestBase, IsolatedAsyncioTestCase):
    """追踪管理器测试"""

    def setUp(self):
        super().setUp()
        self.manager = TracingManager(self.container)

    def test_register_tracer(self):
        """测试注册追踪器"""
        tracer = TestTracer(self.container)
        self.manager.register_tracer("test", tracer)

        # 验证追踪器是否注册成功
        self.assertIn("test", self.manager.get_tracer_types())
        self.assertEqual(self.manager.get_tracer("test"), tracer)

    def test_register_duplicate_tracer(self):
        """测试重复注册追踪器"""
        tracer = TestTracer(self.container)
        self.manager.register_tracer("test", tracer)

        # 验证重复注册是否抛出异常
        with self.assertRaises(ValueError):
            self.manager.register_tracer("test", tracer)

    def test_get_tracer(self):
        """测试获取追踪器"""
        tracer = TestTracer(self.container)
        self.manager.register_tracer("test", tracer)

        # 验证获取追踪器
        self.assertEqual(self.manager.get_tracer("test"), tracer)
        self.assertIsNone(self.manager.get_tracer("non-existent"))

    def test_get_all_tracers(self):
        """测试获取所有追踪器"""
        tracer1 = TestTracer(self.container)
        tracer2 = LLMTracer(self.container)
        self.manager.register_tracer("test1", tracer1)
        self.manager.register_tracer("test2", tracer2)

        tracers = self.manager.get_all_tracers()
        self.assertEqual(len(tracers), 2)
        self.assertEqual(tracers["test1"], tracer1)
        self.assertEqual(tracers["test2"], tracer2)

    def test_initialize_and_shutdown(self):
        """测试初始化和关闭"""
        tracer = TestTracer(self.container)
        self.manager.register_tracer("test", tracer)

        # 测试初始化
        self.manager.initialize()
        
        # 测试关闭
        self.manager.shutdown()

    async def test_websocket_operations(self):
        """测试WebSocket相关操作"""
        tracer = TestTracer(self.container)
        self.manager.register_tracer("test", tracer)

        # 创建一个模拟的WebSocket客户端
        class MockWebSocket:
            def __init__(self):
                self.queue = asyncio.Queue()

        ws = MockWebSocket()

        # 测试注册WebSocket客户端
        queue = self.manager.register_ws_client("test")

        # 测试注销WebSocket客户端
        self.manager.unregister_ws_client("test", queue)

    def test_trace_operations(self):
        """测试追踪操作"""
        tracer = TestTracer(self.container)
        self.manager.register_tracer("test", tracer)

        # 测试获取最近的追踪记录
        traces = self.manager.get_recent_traces("test")
        self.assertEqual(len(traces), 0)

        # 测试获取不存在的追踪器的记录
        with self.assertRaises(ValueError):
            self.manager.get_recent_traces("non-existent")

        # 测试获取特定追踪记录
        trace = self.manager.get_trace_by_id("test", "non-existent-id")
        self.assertIsNone(trace) 