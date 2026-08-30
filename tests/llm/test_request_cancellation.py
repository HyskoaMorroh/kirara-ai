"""需求 21.3：取消必须真的中止上游请求。

`turn_deadline_seconds` 到点后，`llm_manager` 会置位取消信号、让等待循环松手，
并调用 `adapter.cancel_pending_request(request)`。问题是**没有任何适配器实现过
这个方法**——全仓库对它的引用只有 `getattr` 那一处。于是：

- HTTP 连接不会被中止，上游继续生成、继续计费；
- 承载请求的工作线程是 daemon，会一直跑到自然结束；
- 「取消」只对本进程的等待生效，对钱和上游负载完全无效。

这不是「取消没实现」，而是「取消看起来实现了」——最坏的一种形态：日志里写着
已取消，账单上照旧扣钱。

这些用例要求四个预置适配器都能真正中止在途请求。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterator

import pytest
import requests

from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest


def _request(model: str = "m") -> LLMChatRequest:
    return LLMChatRequest(
        model=model,
        messages=[
            LLMChatTextContent(text="hello").model_validate(
                {"type": "text", "text": "hello"}
            )
        ]
        if False
        else [],
    )


ADAPTER_FACTORIES: dict[str, Any] = {}


def _openai_adapter():
    from kirara_ai.plugins.llm_preset_adapters.openai_adapter import (
        OpenAIAdapter,
        OpenAIConfig,
    )

    adapter = OpenAIAdapter(OpenAIConfig(api_key="k"))
    # `media_manager` 由 IoC 注入；这里只走 HTTP 层，给一个占位即可。
    adapter.media_manager = None  # type: ignore[assignment]
    return adapter


def _claude_adapter():
    from kirara_ai.plugins.llm_preset_adapters.claude_adapter import (
        ClaudeAdapter,
        ClaudeConfig,
    )

    return ClaudeAdapter(ClaudeConfig(api_key="k"))


def _gemini_adapter():
    from kirara_ai.plugins.llm_preset_adapters.gemini_adapter import (
        GeminiAdapter,
        GeminiConfig,
    )

    return GeminiAdapter(GeminiConfig(api_key="k"))


def _ollama_adapter():
    from kirara_ai.plugins.llm_preset_adapters.ollama_adapter import (
        OllamaAdapter,
        OllamaConfig,
    )

    return OllamaAdapter(OllamaConfig(api_base="http://127.0.0.1:11434"))


ADAPTERS = {
    "openai": _openai_adapter,
    "claude": _claude_adapter,
    "gemini": _gemini_adapter,
    "ollama": _ollama_adapter,
}


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_every_preset_adapter_can_cancel_a_pending_request(name: str):
    """四家预置适配器都必须真的实现 `cancel_pending_request`。

    `llm_manager` 用 `getattr(adapter, "cancel_pending_request", None)` 调用它，
    因此「没实现」不会报错——只会静默地什么都不做。这条断言是唯一能发现
    「取消看起来实现了」的地方。
    """
    adapter = ADAPTERS[name]()

    cancel = getattr(adapter, "cancel_pending_request", None)
    assert callable(cancel), f"{name} 适配器没有实现 cancel_pending_request"


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_cancelling_an_unknown_request_is_a_no_op(name: str):
    """取消一个从未发出的请求不能抛异常。

    `llm_manager` 在多个位置调用它（超时、取消、deadline），其中有些位置
    请求可能还没真正发出。观测/清理动作抛异常会把一次超时变成一次崩溃。
    """
    adapter = ADAPTERS[name]()

    adapter.cancel_pending_request(_request())  # 不得抛出


def test_cancel_actually_closes_the_underlying_response(monkeypatch):
    """取消必须调用在途响应的 `close()`，而不只是设一个标记。

    `requests` 的流式响应只有 `close()` 会真的断开连接。少了这一步，
    上游会把整段内容生成完——取消在账单上完全不生效。
    """
    adapter = _openai_adapter()
    closed = threading.Event()

    class _Response:
        status_code = 200
        text = ""

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self, decode_unicode: bool = False) -> Iterator[str]:
            # 一直产出直到被关闭：模拟一个还在生成的上游。
            while not closed.is_set():
                time.sleep(0.01)
                yield ""

        def close(self) -> None:
            closed.set()

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: object) -> None:
            self.close()

    response = _Response()
    monkeypatch.setattr(
        adapter._session, "post", lambda *args, **kwargs: response
    )

    request = _request()
    started = threading.Event()

    def consume() -> None:
        started.set()
        try:
            for _ in adapter.stream_chat(request):
                pass
        except Exception:
            pass

    worker = threading.Thread(target=consume, daemon=True)
    worker.start()
    assert started.wait(timeout=2)
    # 给流一点时间进入 iter_lines。
    time.sleep(0.1)

    adapter.cancel_pending_request(request)

    assert closed.wait(timeout=2), "取消没有关闭在途响应，上游会继续生成并计费"
    worker.join(timeout=2)


def test_a_finished_request_is_forgotten_so_the_registry_cannot_grow(monkeypatch):
    """在途请求登记表必须在请求结束后清空。

    每个请求都登记而从不移除，等于一个按请求数增长的 map——一个长期运行的
    部署会把它变成内存泄漏，而症状（内存缓慢上涨）与取消功能毫无表面关联。
    """
    adapter = _openai_adapter()

    class _Response:
        status_code = 200
        text = ""

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self, decode_unicode: bool = False) -> Iterator[str]:
            yield "data: [DONE]"

        def close(self) -> None:
            return None

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        adapter._session, "post", lambda *args, **kwargs: _Response()
    )

    for index in range(5):
        for _ in adapter.stream_chat(_request(model=f"m{index}")):
            pass

    assert not adapter._pending_responses, "请求结束后仍留在登记表里"


def test_non_stream_requests_are_cancellable_too(monkeypatch):
    """非流式路径同样要能被中止。

    非流式请求同样会等上游几十秒，`turn_deadline_seconds` 到点时它也应该松手。
    只支持流式取消等于让默认配置（`reply_stream_mode: off`）完全没有取消能力。
    """
    from kirara_ai.tracing.decorator import suppress_llm_chat_tracing

    adapter = _openai_adapter()
    closed = threading.Event()
    entered = threading.Event()

    class _SlowResponse:
        status_code = 200
        text = "{}"

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            entered.set()
            # 等到被取消再返回，模拟一个慢上游。
            closed.wait(timeout=3)
            raise requests.exceptions.RequestException("cancelled")

        def close(self) -> None:
            closed.set()

    def post(*_args: object, **_kwargs: object) -> _SlowResponse:
        return _SlowResponse()

    monkeypatch.setattr(adapter._session, "post", post)

    request = _request()

    def consume() -> None:
        try:
            # `chat` 带 `@trace_llm_chat`，这里没有 tracer；抑制追踪即可，
            # 本用例关心的是 HTTP 层能否被中止。
            with suppress_llm_chat_tracing():
                adapter.chat(request)
        except Exception:
            pass

    worker = threading.Thread(target=consume, daemon=True)
    worker.start()
    assert entered.wait(timeout=2)

    adapter.cancel_pending_request(request)

    assert closed.wait(timeout=2), "非流式请求无法被取消"
    worker.join(timeout=3)
