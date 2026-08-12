from datetime import datetime

from kirara_ai.events.tracing import LLMRequestCompleteEvent, LLMRequestFailEvent, LLMRequestStartEvent
from tests.tracing.test_base import TracingTestBase


class TestLLMRequestTrace(TracingTestBase):
    """LLM请求追踪记录测试"""

    def setUp(self):
        super().setUp()
        self.trace = self.create_test_trace()

    def test_update_from_start_event(self):
        """测试从开始事件更新"""
        request = self.create_test_request()
        event = LLMRequestStartEvent(
            trace_id="test-trace-id",
            model_id="test-model",
            backend_name="test-backend",
            request=request,
        )

        self.trace.update_from_event(event)

        self.assertEqual(self.trace.trace_id, "test-trace-id")
        self.assertEqual(self.trace.model_id, "test-model")
        self.assertEqual(self.trace.backend_name, "test-backend")
        self.assertEqual(self.trace.status, "pending")
        self.assertIsNotNone(self.trace.request)

    def test_update_from_complete_event(self):
        """测试从完成事件更新"""
        request = self.create_test_request()
        response = self.create_test_response()
        start_time = datetime.now().timestamp()
        event = LLMRequestCompleteEvent(
            trace_id="test-trace-id",
            model_id="test-model",
            backend_name="test-backend",
            request=request,
            response=response,
            start_time=start_time
        )

        self.trace.update_from_event(event)

        self.assertEqual(self.trace.status, "success")
        self.assertEqual(self.trace.prompt_tokens, 10)
        self.assertEqual(self.trace.completion_tokens, 20)
        self.assertEqual(self.trace.total_tokens, 30)
        self.assertIsNotNone(self.trace.response)

    def test_update_from_fail_event(self):
        """测试从失败事件更新"""
        request = self.create_test_request()
        start_time = datetime.now().timestamp()
        event = LLMRequestFailEvent(
            trace_id="test-trace-id",
            model_id="test-model",
            backend_name="test-backend",
            request=request,
            error="Test error",
            start_time=start_time
        )

        self.trace.update_from_event(event)

        self.assertEqual(self.trace.status, "failed")
        self.assertEqual(self.trace.error, "Test error")

    def test_to_dict(self):
        """测试转换为字典"""
        request = self.create_test_request()
        response = self.create_test_response()
        
        # 设置一些基本属性
        self.trace.request = request.model_dump()
        self.trace.response = response.model_dump()
        self.trace.prompt_tokens = 10
        self.trace.completion_tokens = 20
        self.trace.total_tokens = 30

        # 测试基本字典转换
        basic_dict = self.trace.to_dict()
        self.assertEqual(basic_dict["trace_id"], "test-trace-id")
        self.assertEqual(basic_dict["model_id"], "test-model")
        self.assertEqual(basic_dict["backend_name"], "test-backend")
        self.assertEqual(basic_dict["prompt_tokens"], 10)
        self.assertEqual(basic_dict["completion_tokens"], 20)
        self.assertEqual(basic_dict["total_tokens"], 30)

        # 测试详细字典转换
        detail_dict = self.trace.to_detail_dict()
        self.assertIn("request", detail_dict)
        self.assertIn("response", detail_dict)
        self.assertEqual(detail_dict["request"], request.model_dump())
        self.assertEqual(detail_dict["response"], response.model_dump())

    def test_request_response_properties(self):
        """测试请求和响应属性"""
        request = self.create_test_request()
        response = self.create_test_response()

        # 测试请求属性
        self.trace.request = request.model_dump()
        self.assertIsNotNone(self.trace.request)
        self.assertEqual(self.trace.request["model"], "test-model")

        # 测试响应属性
        self.trace.response = response.model_dump()
        self.assertIsNotNone(self.trace.response)
        self.assertEqual(self.trace.response["message"]["content"][0]["text"], "test response")