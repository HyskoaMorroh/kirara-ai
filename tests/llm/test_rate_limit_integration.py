"""限额余量必须真的从请求链路上采集并暴露（需求 9）。

`test_rate_limit.py` 只证明解析函数算得对。函数正确而链路不调用，正是本轮
反复在修的那类缺陷——有定义、有测试、主链路零调用。

因此这里验证两跳：
1. 适配器发完请求后**真的读了响应头**并把快照存下来；
2. `GET /llm/resilience/status` 的行里**真的带上了这个快照**。

第二跳尤其重要：`/llm/resilience/status` 已经是运维面板的数据源，
余量放在别处就等于要求用户再找一个地方看。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.plugins.llm_preset_adapters.claude_adapter import ClaudeAdapter, ClaudeConfig

SUCCESS_BODY = {
    "content": [{"type": "text", "text": "ok"}],
    "role": "assistant",
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 3},
}

RATE_LIMIT_HEADERS = {
    "anthropic-ratelimit-requests-limit": "1000",
    "anthropic-ratelimit-requests-remaining": "12",
    "anthropic-ratelimit-tokens-limit": "80000",
    "anthropic-ratelimit-tokens-remaining": "79000",
}


class _Response:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else SUCCESS_BODY
        self.text = json.dumps(self._body)
        self.headers = dict(headers or {})

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self._body

    def close(self):
        return None


def _adapter(monkeypatch, response) -> ClaudeAdapter:
    adapter = ClaudeAdapter(ClaudeConfig(api_key="k", api_base="https://example.invalid/v1"))
    adapter.media_manager = MagicMock()
    adapter.tracer = MagicMock()
    adapter.backend_name = "claude"
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.claude_adapter.requests.post",
        lambda *_a, **_k: response,
    )
    return adapter


def _request() -> LLMChatRequest:
    return LLMChatRequest(
        messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="hi")])],
        model="claude-sonnet-5",
        max_tokens=1000,
    )



def _manager():
    """装一个最小可用的 `LLMManager`。

    `LLMManager` 从容器里解析 `LLMBackendRegistry` 与 `EventBus`；少任何一个
    都是 `KeyError`，而那与限额余量无关。
    """
    from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
    from kirara_ai.events.event_bus import EventBus
    from kirara_ai.ioc.container import DependencyContainer
    from kirara_ai.llm.llm_manager import LLMManager
    from kirara_ai.llm.llm_registry import LLMBackendRegistry

    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    config.llms.api_backends = [
        LLMBackendConfig(name="claude", adapter="claude", enable=True)
    ]
    container.register(GlobalConfig, config)
    container.register(LLMBackendRegistry, LLMBackendRegistry())
    container.register(EventBus, EventBus())
    return LLMManager(container)


def test_the_adapter_records_the_upstream_headroom(monkeypatch):
    """响应头里的余量此前被完整丢弃：适配器从不读 `response.headers`。

    后果不是少一个图表，而是限流只能事后发现——请求开始报 429 才知道撞了上限，
    而那时排队与重试已经在发生。
    """
    adapter = _adapter(monkeypatch, _Response(headers=RATE_LIMIT_HEADERS))

    adapter.chat(_request())

    snapshot = adapter.last_rate_limit
    assert snapshot is not None, "适配器没有采集限额头"
    assert snapshot.remaining_requests == 12
    assert snapshot.request_headroom == pytest.approx(0.012)


def test_an_upstream_that_reports_nothing_leaves_it_unset(monkeypatch):
    """很多兼容端点不返回限额头。此时必须是 `None` 而不是一组 0——
    0 表示「余量用完」，是最该报警的状态。"""
    adapter = _adapter(monkeypatch, _Response(headers={"content-type": "application/json"}))

    adapter.chat(_request())

    assert adapter.last_rate_limit is None


def test_a_failed_request_still_records_headroom(monkeypatch):
    """429 那次的响应头恰恰是最有价值的一次——它带着 `retry-after`。"""
    adapter = _adapter(
        monkeypatch,
        _Response(
            status_code=429,
            body={"error": {"message": "rate limited"}},
            headers={"retry-after": "30", "x-ratelimit-remaining-requests": "0"},
        ),
    )

    with pytest.raises(Exception):
        adapter.chat(_request())

    snapshot = adapter.last_rate_limit
    assert snapshot is not None
    assert snapshot.retry_after_seconds == pytest.approx(30.0)
    assert snapshot.remaining_requests == 0


def test_resilience_status_exposes_the_headroom():
    """余量必须出现在已有的运维面板数据源里。

    `/llm/resilience/status` 已经是那个面板的数据源；把余量放在别处就等于
    要求用户再找一个地方看，而「离上限还有多远」和「熔断开没开」是同一个问题的
    两个侧面——都在回答「这家现在能不能用」。
    """
    from kirara_ai.llm.rate_limit import RateLimitSnapshot

    manager = _manager()

    adapter = MagicMock()
    adapter.backend_name = "claude"
    adapter.last_rate_limit = RateLimitSnapshot(
        limit_requests=1000, remaining_requests=12
    )
    manager.active_backends = {"claude-sonnet-5": [adapter]}

    rows = manager.get_resilience_status()

    assert rows, "没有返回任何供应商行"
    row = rows[0]
    assert "rate_limit" in row, "resilience status 里没有限额余量"
    assert row["rate_limit"]["remaining_requests"] == 12
    assert row["rate_limit"]["request_headroom"] == pytest.approx(0.012)


def test_resilience_status_reports_none_when_upstream_is_silent():
    """没有余量数据时该项为 `None`，界面据此显示「未上报」而不是 0。"""
    manager = _manager()

    adapter = MagicMock()
    adapter.backend_name = "claude"
    adapter.last_rate_limit = None
    manager.active_backends = {"claude-sonnet-5": [adapter]}

    assert manager.get_resilience_status()[0]["rate_limit"] is None
