"""隐藏 AI 署名必须真正作用在**投递给用户的回复**上（需求 8）。

一个只写进配置、没人读的 `hide_ai_attribution` 布尔量，正是本轮反复在修的那类
缺陷（`UsageSource.ESTIMATED` 曾经有定义、有测试、主链路零调用）。因此这里断言
执行链路本身：开关打开时，`execute_chat` 返回的 `LLMChatResponse` 里的**文本内容**
已经不含署名；关闭时逐字符不变。

同时钉住三条不能出错的边界：

* 生效范围是**该供应商**。队列里 P1 开了、P2 没开时，两者各自按自己的配置处理，
  否则「哪条回复被改写过」取决于故障转移走到了第几家。
* 只改文本片段，**不动工具调用**。改写 `LLMToolCallContent` 的参数会让工具收到
  被篡改的输入。
* 用量与成本**不受影响**。署名是上游已经生成并计费的 token，
  把它从展示里去掉不等于没花那笔钱——改写 usage 会让账单对不上。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import (LLMChatMessage, LLMChatTextContent,
                                          LLMToolCallContent)
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import (LLMChatResponse, Message, Usage,
                                           UsageSource)
from kirara_ai.llm.llm_manager import LLMManager

SIGNED_REPLY = "作为一个 AI 助手，我建议你先备份。\n\n本回复由 AI 生成。"


class StubAdapter:
    def __init__(self, backend_name: str, response: LLMChatResponse):
        self.backend_name = backend_name
        self._response = response
        self.calls = 0

    def chat(self, _request: LLMChatRequest) -> LLMChatResponse:
        self.calls += 1
        return self._response


def signed_response(*, usage: Usage | None = None) -> LLMChatResponse:
    return LLMChatResponse(
        model="mock-model",
        usage=usage,
        message=Message(
            content=[LLMChatTextContent(text=SIGNED_REPLY)],
            role="assistant",
            finish_reason="stop",
        ),
    )


def tool_call_response() -> LLMChatResponse:
    return LLMChatResponse(
        model="mock-model",
        message=Message(
            content=[
                LLMChatTextContent(text="作为一个 AI 助手，我来查一下。"),
                LLMToolCallContent(
                    id="call-1",
                    name="lookup",
                    parameters={"query": "本回复由 AI 生成"},
                ),
            ],
            role="assistant",
            finish_reason="tool_calls",
        ),
    )


def backend(name: str, *, hide: bool | None, priority: int) -> LLMBackendConfig:
    fields: dict[str, Any] = {
        "name": name,
        "adapter": "openai",
        "priority": priority,
        "models": [{"id": "mock-model", "type": "llm", "ability": 2}],
    }
    if hide is not None:
        fields["hide_ai_attribution"] = hide
    return LLMBackendConfig(**fields)


def make_manager(
    backends: list[LLMBackendConfig], adapters: dict[str, StubAdapter]
) -> LLMManager:
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
    manager.active_backends = {
        "mock-model": [adapters[item.name] for item in backends]
    }
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


def reply_text(response: LLMChatResponse) -> str:
    return "".join(
        part.text
        for part in response.message.content
        if isinstance(part, LLMChatTextContent)
    )


def test_disabled_provider_delivers_the_reply_verbatim():
    """默认关闭：回复逐字符不变。"""
    adapter = StubAdapter("p1", signed_response())
    manager = make_manager([backend("p1", hide=None, priority=1)], {"p1": adapter})

    result = manager.execute_chat(chat_request())

    assert reply_text(result.response) == SIGNED_REPLY


def test_enabled_provider_strips_attribution_from_the_delivered_reply():
    """开关打开时，返回给调用方的文本里已经没有署名。"""
    adapter = StubAdapter("p1", signed_response())
    manager = make_manager([backend("p1", hide=True, priority=1)], {"p1": adapter})

    result = manager.execute_chat(chat_request())

    text = reply_text(result.response)
    assert "我建议你先备份。" in text
    assert "AI 助手" not in text
    assert "由 AI 生成" not in text


def test_the_setting_applies_per_provider_not_globally():
    """P1 开了、P2 没开时，走到 P2 的那次回复不得被改写。"""
    failing = StubAdapter("p1", signed_response())

    def explode(_request: LLMChatRequest):
        failing.calls += 1
        raise TimeoutError("upstream timed out")

    failing.chat = explode  # type: ignore[method-assign]
    secondary = StubAdapter("p2", signed_response())
    manager = make_manager(
        [backend("p1", hide=True, priority=1), backend("p2", hide=None, priority=2)],
        {"p1": failing, "p2": secondary},
    )

    result = manager.execute_chat(chat_request())

    assert secondary.calls == 1
    assert reply_text(result.response) == SIGNED_REPLY


def test_tool_call_parameters_are_never_rewritten():
    """工具参数是给程序读的，改写会让工具收到被篡改的输入。"""
    adapter = StubAdapter("p1", tool_call_response())
    manager = make_manager([backend("p1", hide=True, priority=1)], {"p1": adapter})

    result = manager.execute_chat(chat_request())

    calls = [
        part
        for part in result.response.message.content
        if isinstance(part, LLMToolCallContent)
    ]
    assert len(calls) == 1
    assert calls[0].parameters == {"query": "本回复由 AI 生成"}
    # 文本片段仍然要被清理。
    assert "AI 助手" not in reply_text(result.response)


def test_usage_and_source_are_left_alone():
    """署名是上游已经生成并计费的 token；改写 usage 会让账单对不上。"""
    usage = Usage(
        prompt_tokens=30,
        completion_tokens=12,
        total_tokens=42,
        source=UsageSource.PROVIDER,
    )
    adapter = StubAdapter("p1", signed_response(usage=usage))
    manager = make_manager([backend("p1", hide=True, priority=1)], {"p1": adapter})

    result = manager.execute_chat(chat_request())

    assert result.response.usage is not None
    assert result.response.usage.total_tokens == 42
    assert result.response.usage.completion_tokens == 12
    assert result.response.usage.source is UsageSource.PROVIDER


def test_backend_config_defaults_to_disabled():
    assert LLMBackendConfig(name="p", adapter="openai").hide_ai_attribution is False
    assert (
        LLMBackendConfig(
            name="p", adapter="openai", hide_ai_attribution=True
        ).hide_ai_attribution
        is True
    )
