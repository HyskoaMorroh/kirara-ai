from datetime import datetime

from kirara_ai.events.tracing import LLMRequestCompleteEvent, LLMRequestFailEvent, LLMRequestStartEvent
from kirara_ai.tracing import LLMTracer
from tests.tracing.test_base import TracingTestBase


class TestLLMTracer(TracingTestBase):
    """LLM追踪器测试"""

    def setUp(self):
        super().setUp()
        self.tracer = LLMTracer(self.container)
        self.tracer.initialize()

    def tearDown(self):
        self.tracer.shutdown()
        super().tearDown()

    def test_start_request_tracking(self):
        """测试开始追踪请求"""
        request = self.create_test_request()
        trace_id = self.tracer.start_request_tracking("test-backend", request)

        # 验证追踪ID是否生成
        self.assertIsNotNone(trace_id)
        # 验证活跃追踪是否记录
        self.assertIn(trace_id, self.tracer._active_traces)
        # 验证事件是否发布
        trace = self.tracer.get_trace_by_id(trace_id)
        self.assertIsNotNone(trace)
        self.assertEqual(trace.status, "pending")

    def test_complete_request_tracking(self):
        """测试完成追踪请求"""
        request = self.create_test_request()
        response = self.create_test_response()
        trace_id = self.tracer.start_request_tracking("test-backend", request)

        self.tracer.complete_request_tracking(trace_id, request, response)

        # 验证追踪记录是否更新
        trace = self.tracer.get_trace_by_id(trace_id)
        self.assertIsNotNone(trace)
        self.assertEqual(trace.status, "success")
        self.assertEqual(trace.total_tokens, 30)
        # 验证活跃追踪是否移除
        self.assertNotIn(trace_id, self.tracer._active_traces)

    def test_fail_request_tracking(self):
        """测试失败追踪请求"""
        request = self.create_test_request()
        error = Exception("Test error")
        trace_id = self.tracer.start_request_tracking("test-backend", request)

        self.tracer.fail_request_tracking(trace_id, request, str(error))

        # 验证追踪记录是否更新
        trace = self.tracer.get_trace_by_id(trace_id)
        self.assertIsNotNone(trace)
        self.assertEqual(trace.status, "failed")
        self.assertEqual(trace.error, str(error))
        # 验证活跃追踪是否移除
        self.assertNotIn(trace_id, self.tracer._active_traces)

    def test_event_handlers(self):
        """测试事件处理程序"""
        request = self.create_test_request()
        response = self.create_test_response()
        trace_id = "test-trace-id"

        # 测试开始事件处理
        start_event = LLMRequestStartEvent(
            trace_id=trace_id,
            model_id="test-model",
            backend_name="test-backend",
            request=request
        )
        self.event_bus.post(start_event)

        trace = self.tracer.get_trace_by_id(trace_id)
        self.assertIsNotNone(trace)
        self.assertEqual(trace.status, "pending")

        # 测试完成事件处理
        complete_event = LLMRequestCompleteEvent(
            trace_id=trace_id,
            model_id="test-model",
            backend_name="test-backend",
            request=request,
            response=response,
            start_time=datetime.now().timestamp()
        )
        self.event_bus.post(complete_event)

        trace = self.tracer.get_trace_by_id(trace_id)
        self.assertEqual(trace.status, "success")

        # 测试失败事件处理
        fail_event = LLMRequestFailEvent(
            trace_id=trace_id,
            model_id="test-model",
            backend_name="test-backend",
            request=request,
            error="Test error",
            start_time=datetime.now().timestamp()
        )
        self.event_bus.post(fail_event)

        trace = self.tracer.get_trace_by_id(trace_id)
        self.assertEqual(trace.status, "failed")

    def test_get_statistics(self):
        """测试获取统计信息"""
        # 创建一些测试数据
        for i in range(3):
            request = self.create_test_request()
            trace_id = self.tracer.start_request_tracking("test-backend", request)
            if i < 2:
                response = self.create_test_response()
                self.tracer.complete_request_tracking(trace_id, request, response)
            else:
                self.tracer.fail_request_tracking(trace_id, request, "Test error")

        stats = self.tracer.get_statistics()

        # 验证基本统计信息
        self.assertEqual(stats["overview"]["total_requests"], 3)
        self.assertEqual(stats["overview"]["success_requests"], 2)
        self.assertEqual(stats["overview"]["failed_requests"], 1)
        self.assertEqual(stats["overview"]["total_tokens"], 60)  # 2 * 30 tokens

        # 验证模型统计信息
        self.assertTrue(len(stats["models"]) > 0)
        model_stat = stats["models"][0]
        self.assertEqual(model_stat["model_id"], "test-model")
        self.assertEqual(model_stat["count"], 3)

        # 验证后端统计信息
        self.assertTrue(len(stats["backends"]) > 0)
        backend_stat = stats["backends"][0]
        self.assertEqual(backend_stat["backend_name"], "test-backend")
        self.assertEqual(backend_stat["count"], 3) 