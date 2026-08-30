"""流式聚合后的署名清理必须与非流式同口径（需求 8）。

流式路径不能逐分片清理：一句「本回复由 AI 生成。」很可能被切成
`本回复由 ` / `AI 生成。` 两片，逐片判断两片都不像署名，于是整句原样漏出去——
用户看到的结果取决于上游怎么切分片，这是最难复现的一类缺陷。

正确的位置是**聚合完成之后**、按那次真正成交的供应商配置执行一次。
这些用例钉住这一点，以及「分片本身不得被改写」（否则首字节时刻会失真）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Iterator

from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.resilience import ProviderAttempt


def chunk(text: str) -> LLMChatResponse:
    return LLMChatResponse(
        model="mock-model",
        message=Message(
            content=[LLMChatTextContent(text=text)], role="assistant", finish_reason=""
        ),
    )


class StreamingAdapter:
    """把署名切成两片返回，模拟真实 SSE 的切分位置。"""

    def __init__(self, backend_name: str, pieces: list[str]):
        self.backend_name = backend_name
        self.pieces = pieces

    def stream_chat(self, _request: LLMChatRequest) -> Iterator[LLMChatResponse]:
        for piece in self.pieces:
            yield chunk(piece)


def backend(name: str, *, hide: bool) -> LLMBackendConfig:
    fields: dict[str, Any] = {
        "name": name,
        "adapter": "openai",
        "priority": 1,
        "hide_ai_attribution": hide,
        "models": [{"id": "mock-model", "type": "llm", "ability": 2}],
    }
    return LLMBackendConfig(**fields)


def make_manager(backends: list[LLMBackendConfig], adapters: list[Any]) -> LLMManager:
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    config.llms.api_backends = backends
    container.register(GlobalConfig, config)

    manager = object.__new__(LLMManager)
    manager.container = container
    manager.config = config
    manager.logger = SimpleNamespace(
        debug=lambda *a, **k: None,
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        opt=lambda **k: SimpleNamespace(error=lambda *a, **kw: None),
    )
    manager._resilience_breakers = {}
    manager._resilience_attempts = {}
    manager.active_backends = {"mock-model": adapters}
    manager._get_llm_tracer = lambda: None  # type: ignore[method-assign]
    manager._start_logical_trace = lambda *a, **k: None  # type: ignore[method-assign]
    manager._complete_logical_trace = lambda *a, **k: None  # type: ignore[method-assign]
    manager._fail_logical_trace = lambda *a, **k: None  # type: ignore[method-assign]
    manager._calculate_cost_snapshot = lambda *a, **k: None  # type: ignore[method-assign]
    return manager


def chat_request() -> LLMChatRequest:
    return LLMChatRequest(
        messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="hi")])],
        model="mock-model",
    )


def aggregated(response_text: str) -> LLMChatResponse:
    return LLMChatResponse(
        model="mock-model",
        message=Message(
            content=[LLMChatTextContent(text=response_text)],
            role="assistant",
            finish_reason="stop",
        ),
    )


def test_chunks_are_never_rewritten_mid_stream():
    """分片必须原样交付：逐片改写既不可靠，也会影响首字节判定。"""
    adapter = StreamingAdapter("p1", ["结论：可以。\n\n本回复由 ", "AI 生成。"])
    manager = make_manager([backend("p1", hide=True)], [adapter])

    pieces = [
        part.text
        for item in manager.execute_stream(chat_request())
        for part in item.message.content
        if isinstance(part, LLMChatTextContent)
    ]

    assert pieces == ["结论：可以。\n\n本回复由 ", "AI 生成。"]


def test_policy_applied_after_aggregation_removes_a_split_signature():
    """聚合之后再清理，才能抓到被切成两片的署名。"""
    adapter = StreamingAdapter("p1", ["结论：可以。\n\n本回复由 ", "AI 生成。"])
    manager = make_manager([backend("p1", hide=True)], [adapter])

    execution = manager.execute_stream(chat_request())
    text = "".join(
        part.text
        for item in execution
        for part in item.message.content
        if isinstance(part, LLMChatTextContent)
    )

    cleaned = manager.apply_response_policy_for_attempts(
        aggregated(text), execution.attempts
    )

    result = "".join(
        part.text
        for part in cleaned.message.content
        if isinstance(part, LLMChatTextContent)
    )
    assert "结论：可以。" in result
    assert "由 AI 生成" not in result


def test_policy_uses_the_provider_that_actually_served_the_stream():
    """按成交的那家配置执行，而不是队列里的第一家。"""
    adapter = StreamingAdapter("p1", ["本回复由 AI 生成。"])
    manager = make_manager([backend("p1", hide=False)], [adapter])

    execution = manager.execute_stream(chat_request())
    list(execution)

    cleaned = manager.apply_response_policy_for_attempts(
        aggregated("本回复由 AI 生成。"), execution.attempts
    )
    text = "".join(
        part.text
        for part in cleaned.message.content
        if isinstance(part, LLMChatTextContent)
    )
    assert text == "本回复由 AI 生成。"


def test_no_successful_attempt_leaves_the_response_untouched():
    """没有成功尝试时不猜供应商：宁可不清理，也不能按别人的配置改写。"""
    manager = make_manager([backend("p1", hide=True)], [StreamingAdapter("p1", [])])

    failed = ProviderAttempt(
        trace_id="t",
        model="mock-model",
        provider="p1",
        attempt=1,
        retry_index=0,
        success=False,
        started_at=0.0,
        completed_at=1.0,
    )

    cleaned = manager.apply_response_policy_for_attempts(
        aggregated("本回复由 AI 生成。"), [failed]
    )
    text = "".join(
        part.text
        for part in cleaned.message.content
        if isinstance(part, LLMChatTextContent)
    )
    assert text == "本回复由 AI 生成。"


def test_empty_attempts_are_safe():
    manager = make_manager([backend("p1", hide=True)], [StreamingAdapter("p1", [])])

    cleaned = manager.apply_response_policy_for_attempts(aggregated("正文"), [])

    assert cleaned.message.content[0].text == "正文"  # type: ignore[union-attr]
