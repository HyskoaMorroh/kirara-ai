"""供应商级 `reasoning_effort` 必须在故障转移队列里逐个生效（需求 8/21）。

配置项挂在**每个供应商**上，而同一个模型可以由多个供应商提供：队列里
P1 是自建高强度推理网关、P2 是不支持思考的兼容接口时，两者必须各自按自己的
配置发请求。把强度写进请求对象一次然后全队列复用，等于让 P1 的设置泄漏到 P2，
而 P2 收到未知字段可能直接 400——一次本可成功的故障转移变成两连败。

同时钉住优先级：调用方在 `LLMChatRequest` 上显式给出的强度**高于**供应商默认值。
反过来会让「这一次想快一点」无法表达。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.llm_manager import LLMManager


def text_response(text: str = "ok") -> LLMChatResponse:
    return LLMChatResponse(
        model="mock-model",
        message=Message(
            content=[LLMChatTextContent(text=text)], role="assistant", finish_reason="stop"
        ),
    )


class RecordingAdapter:
    """记录收到的 `reasoning_effort`，可选地在首次调用时失败。"""

    def __init__(self, backend_name: str, *, fail_first: bool = False):
        self.backend_name = backend_name
        self.seen: list[Any] = []
        self.fail_first = fail_first

    def chat(self, request: LLMChatRequest) -> LLMChatResponse:
        self.seen.append(request.reasoning_effort)
        if self.fail_first:
            self.fail_first = False
            raise TimeoutError("upstream timed out")
        return text_response()


def make_manager(
    backends: list[LLMBackendConfig], adapters: dict[str, RecordingAdapter]
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
    # `active_backends` 的键是**模型 ID**，值是按该模型可用的适配器列表；
    # 队列顺序由 `priority` 决定，不由这里的插入顺序决定。
    manager.active_backends = {
        "mock-model": [adapters[backend.name] for backend in backends]
    }
    return manager


def chat_request(**overrides) -> LLMChatRequest:
    return LLMChatRequest(
        messages=[
            LLMChatMessage(role="user", content=[LLMChatTextContent(text="hi")])
        ],
        model="mock-model",
        **overrides,
    )


def backend(name: str, *, effort: str | None, priority: int) -> LLMBackendConfig:
    fields: dict[str, Any] = {
        "name": name,
        "adapter": "openai",
        "priority": priority,
        "models": [{"id": "mock-model", "type": "llm", "ability": 2}],
    }
    if effort is not None:
        fields["reasoning_effort"] = effort
    return LLMBackendConfig(**fields)


def execute(manager: LLMManager, request: LLMChatRequest):
    """走真实入口 `execute_chat`，但跳过与本主题无关的追踪落库。"""
    manager._get_llm_tracer = lambda: None  # type: ignore[method-assign]
    manager._start_logical_trace = lambda *a, **k: None  # type: ignore[method-assign]
    manager._complete_logical_trace = lambda *a, **k: None  # type: ignore[method-assign]
    manager._fail_logical_trace = lambda *a, **k: None  # type: ignore[method-assign]
    manager._calculate_cost_snapshot = lambda *a, **k: None  # type: ignore[method-assign]
    return manager.execute_chat(request)


def test_provider_reasoning_effort_is_applied_per_provider():
    """P1 与 P2 各自按自己的配置发请求，互不影响。"""
    primary = RecordingAdapter("p1", fail_first=True)
    secondary = RecordingAdapter("p2")
    manager = make_manager(
        [
            backend("p1", effort="max", priority=1),
            backend("p2", effort=None, priority=2),
        ],
        {"p1": primary, "p2": secondary},
    )

    result = execute(manager, chat_request())

    assert result.response.message.content
    assert primary.seen == ["max"], "P1 未收到自己配置的推理强度"
    assert secondary.seen == [None], "P1 的强度泄漏到了不支持它的 P2"


def test_an_explicit_request_effort_wins_over_the_provider_default():
    """调用方显式给出的强度优先，否则「这一次想快一点」无法表达。"""
    adapter = RecordingAdapter("p1")
    manager = make_manager([backend("p1", effort="max", priority=1)], {"p1": adapter})

    execute(manager, chat_request(reasoning_effort="low"))

    assert adapter.seen == ["low"]


def test_the_original_request_object_is_not_mutated():
    """供应商覆盖必须作用在副本上：调用方持有的请求对象不能被改写。

    同一个请求对象会在故障转移队列里被复用；就地改写它会让「上一个供应商的
    设置」跟着走到下一个供应商。
    """
    adapter = RecordingAdapter("p1")
    manager = make_manager([backend("p1", effort="max", priority=1)], {"p1": adapter})
    request = chat_request()

    execute(manager, request)

    assert request.reasoning_effort is None
    assert adapter.seen == ["max"]


def test_no_provider_effort_leaves_the_request_untouched():
    """未配置时不写入任何值，行为与新增该字段之前完全一致。"""
    adapter = RecordingAdapter("p1")
    manager = make_manager([backend("p1", effort=None, priority=1)], {"p1": adapter})

    execute(manager, chat_request())

    assert adapter.seen == [None]


@pytest.mark.parametrize("effort", ["low", "medium", "high", "max"])
def test_every_supported_tier_reaches_the_adapter(effort: str):
    adapter = RecordingAdapter("p1")
    manager = make_manager([backend("p1", effort=effort, priority=1)], {"p1": adapter})

    execute(manager, chat_request())

    assert adapter.seen == [effort]
