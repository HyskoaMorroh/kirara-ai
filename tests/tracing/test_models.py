from datetime import datetime
from decimal import Decimal

from kirara_ai.events.tracing import LLMRequestCompleteEvent, LLMRequestFailEvent, LLMRequestStartEvent
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent, LLMToolCallContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.format.response import Usage, UsageSource
from kirara_ai.llm.pricing import CostSnapshot
from kirara_ai.llm.resilience import ProviderAttempt
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
            correlation_id="turn-model-start",
        )

        self.trace.update_from_event(event)

        self.assertEqual(self.trace.trace_id, "test-trace-id")
        self.assertEqual(self.trace.model_id, "test-model")
        self.assertEqual(self.trace.backend_name, "test-backend")
        self.assertEqual(self.trace.provider, "test-backend")
        self.assertEqual(self.trace.correlation_id, "turn-model-start")
        self.assertEqual(self.trace.status, "pending")
        self.assertEqual(self.trace.usage_source, "unknown")
        self.assertIsNone(self.trace.error_category)
        self.assertIsNotNone(self.trace.request)

    def test_complete_event_preserves_start_correlation_id_in_all_projections(self):
        request = self.create_test_request()
        self.trace.update_from_event(
            LLMRequestStartEvent(
                trace_id="test-trace-id",
                model_id="test-model",
                backend_name="test-backend",
                request=request,
                correlation_id="turn-complete",
            )
        )

        self.trace.update_from_event(
            LLMRequestCompleteEvent(
                trace_id="test-trace-id",
                model_id="test-model",
                backend_name="test-backend",
                request=request,
                response=self.create_test_response(),
                start_time=datetime.now().timestamp(),
            )
        )

        self.assertEqual(self.trace.correlation_id, "turn-complete")
        self.assertEqual(self.trace.to_dict()["correlation_id"], "turn-complete")
        self.assertEqual(
            self.trace.to_detail_dict()["correlation_id"], "turn-complete"
        )

    def test_fail_event_preserves_start_correlation_id_in_all_projections(self):
        request = self.create_test_request()
        self.trace.update_from_event(
            LLMRequestStartEvent(
                trace_id="test-trace-id",
                model_id="test-model",
                backend_name="test-backend",
                request=request,
                correlation_id="turn-fail",
            )
        )

        self.trace.update_from_event(
            LLMRequestFailEvent(
                trace_id="test-trace-id",
                model_id="test-model",
                backend_name="test-backend",
                request=request,
                error="provider unavailable",
                start_time=datetime.now().timestamp(),
            )
        )

        self.assertEqual(self.trace.correlation_id, "turn-fail")
        self.assertEqual(self.trace.to_dict()["correlation_id"], "turn-fail")
        self.assertEqual(self.trace.to_detail_dict()["correlation_id"], "turn-fail")

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

    def test_complete_event_persists_usage_attempt_ttft_and_cost_snapshot(self):
        request = self.create_test_request()
        response = self.create_test_response(
            Usage(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                cached_tokens=2,
                cache_write_tokens=1,
                source=UsageSource.PROVIDER,
            )
        )
        attempt = ProviderAttempt(
            trace_id="test-trace-id",
            model="test-model",
            provider="test-backend",
            attempt=1,
            retry_index=0,
            success=True,
            started_at=10.0,
            first_byte_at=10.25,
            completed_at=11.0,
        )
        cost = CostSnapshot(
            price_version_id="price-v1",
            provider="test-backend",
            model="test-model",
            currency="USD",
            priced_at=datetime.now().astimezone(),
            input_tokens=7,
            output_tokens=20,
            cache_read_tokens=2,
            cache_write_tokens=1,
            input_cost=Decimal("0.1"),
            output_cost=Decimal("0.2"),
            cache_read_cost=Decimal("0.01"),
            cache_write_cost=Decimal("0.02"),
            total_cost=Decimal("0.33"),
            usage_source=UsageSource.PROVIDER,
        )
        event = LLMRequestCompleteEvent(
            trace_id="test-trace-id",
            model_id="test-model",
            backend_name="test-backend",
            request=request,
            response=response,
            start_time=datetime.now().timestamp(),
            attempts=[attempt],
            cost_snapshot=cost,
        )

        self.trace.update_from_event(event)
        payload = self.trace.to_dict()

        self.assertEqual(payload["usage_source"], "provider")
        self.assertEqual(payload["provider"], "test-backend")
        self.assertIsNone(payload["error_category"])
        self.assertEqual(payload["cache_write_tokens"], 1)
        self.assertEqual(payload["ttft_ms"], 250)
        self.assertEqual(payload["attempt_count"], 1)
        self.assertEqual(payload["attempts"][0]["provider"], "test-backend")
        self.assertEqual(payload["cost_snapshot"]["price_version_id"], "price-v1")
        self.assertEqual(payload["cost_snapshot"]["total_cost"], "0.33")

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
        self.assertEqual(self.trace.provider, "test-backend")
        self.assertEqual(self.trace.error_category, "unknown")
        self.assertEqual(self.trace.usage_source, "unknown")

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

    def test_trace_content_is_recursively_redacted_before_serialization(self):
        request = LLMChatRequest(
            model="test-model",
            messages=[
                LLMChatMessage(
                    role="assistant",
                    content=[
                        LLMChatTextContent(text="keep this request context"),
                        LLMToolCallContent(
                            name="lookup",
                            parameters={
                                "api_key": "request-secret",
                                "nested": {
                                    "authorization": "Bearer nested-secret",
                                    "query": "keep this query",
                                },
                            },
                        ),
                    ],
                )
            ],
        )
        response = LLMChatResponse(
            model="test-model",
            message=Message(
                role="assistant",
                content=[
                    LLMChatTextContent(
                        text="Authorization: Bearer response-secret; keep this answer"
                    )
                ],
            ),
        )

        self.trace.request = request.model_dump()
        self.trace.response = response.model_dump()

        serialized = f"{self.trace.request_json}\n{self.trace.response_json}"
        self.assertNotIn("request-secret", serialized)
        self.assertNotIn("nested-secret", serialized)
        self.assertNotIn("response-secret", serialized)
        self.assertIn("keep this request context", serialized)
        self.assertIn("keep this query", serialized)
        self.assertIn("keep this answer", serialized)

    def test_trace_redaction_preserves_token_usage_fields(self):
        self.trace.request = {
            "max_tokens": 2048,
            "prompt_tokens": 120,
            "total_tokens": 180,
            "access_token": "private-value",
        }

        assert self.trace.request == {
            "max_tokens": 2048,
            "prompt_tokens": 120,
            "total_tokens": 180,
            "access_token": "[redacted]",
        }

    def test_failure_and_attempt_details_are_redacted_before_serialization(self):
        request = self.create_test_request()
        attempt = ProviderAttempt(
            trace_id="test-trace-id",
            model="test-model",
            provider="test-backend",
            attempt=1,
            retry_index=0,
            success=False,
            error_summary="api_key=attempt-secret upstream unavailable",
        )
        event = LLMRequestFailEvent(
            trace_id="test-trace-id",
            model_id="test-model",
            backend_name="test-backend",
            request=request,
            error="Authorization: Bearer failure-secret cookie=session-secret",
            start_time=datetime.now().timestamp(),
            attempts=[attempt],
        )

        self.trace.update_from_event(event)

        serialized = f"{self.trace.error}\n{self.trace.attempts_json}"
        self.assertNotIn("attempt-secret", serialized)
        self.assertNotIn("failure-secret", serialized)
        self.assertNotIn("session-secret", serialized)
        self.assertIn("upstream unavailable", serialized)
