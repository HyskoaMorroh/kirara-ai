"""WebUI 在线对话必须能看到流式输出（需求 4）。

需求 4 的原文是「本项目必须要实现流式和非流式输出，切合当今市场最新AI的需求」。
当前实现里「流式」只到达上游：``aggregate`` 把流在服务端吃完再发一条完整消息，
``incremental`` 只有 Telegram 能兑现（靠 ``editMessageText``）。而项目**自己的**
WebUI 在线对话是一次性 ``POST /llm/chat``，后端没有任何聊天 SSE 路由——
浏览器里 ``EventSource`` / ``fetch`` 流读天生可用，这个渠道没有平台限制可讲，
它反而是四个入口里最该能看到流式的一个。

这一组测试钉住 SSE 契约本身：事件形状、增量语义、错误也必须落在流里
（HTTP 头已经发出去之后不能再改状态码，错误只能作为事件送达），
以及非流式路径逐字节不变——需求 4 要的是「流式**和**非流式」，不是二选一。
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import pytest

from kirara_ai.agent_runtime import (AgentDefinition, AgentRegistry,
                                    AgentRuntimeExecutor, RuntimeResult,
                                    RuntimeStatus)
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.web.api.llm.webui_adapter import WebUIAdapter
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher
from kirara_ai.workflow.core.dispatch.models.dispatch_rules import \
    CombinedDispatchRule
from kirara_ai.workflow.core.dispatch.registry import DispatchRuleRegistry
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry

#: 认证头。``MockAuthService`` 接受任意非空 Bearer token。
AUTH = {"Authorization": "Bearer mock_token"}


class _ChatWorkflowRegistry:
    def get_workflow(self, workflow_id, container):
        return object()


class _NonStreamingRuntime:
    """A runtime that never pushes increments — the ``off`` mode shape."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any, dict[str, Any]]] = []

    async def run(self, context, message, **options):
        self.calls.append((context, message, options))
        return RuntimeResult(
            status=RuntimeStatus.COMPLETED,
            text=f"reply:{message.content}",
            context=context,
            agent_id=options.get("session_agent_id"),
        )

    async def confirm(self, confirmation_id, context):  # pragma: no cover
        raise AssertionError("confirm must not be reached here")


def _sse_events(body: str) -> list[dict[str, Any]]:
    """把 SSE 响应体解析成事件列表。

    只认 ``event:``/``data:`` 两个字段——这条流不需要 id 与 retry，
    多解析一个字段等于把测试绑在一个契约没有承诺的东西上。
    """
    events: list[dict[str, Any]] = []
    name: str | None = None
    payload: list[str] = []
    for raw in body.split("\n"):
        line = raw.rstrip("\r")
        if line.startswith("event:"):
            name = line[len("event:"):].strip()
            continue
        if line.startswith("data:"):
            payload.append(line[len("data:"):].strip())
            continue
        if not line and name is not None:
            events.append({"event": name, "data": json.loads("".join(payload) or "{}")})
            name, payload = None, []
    if name is not None:
        events.append({"event": name, "data": json.loads("".join(payload) or "{}")})
    return events


class _StreamingRuntime:
    """A runtime that pushes text through the adapter's incremental protocol."""

    def __init__(self, pieces: Iterator[str] | None = None, *, fail: bool = False) -> None:
        self.pieces = list(pieces or ["模拟", "回火", "算法"])
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def run(self, context, message, **kwargs):
        self.calls.append({"context": context, "message": message, **kwargs})
        channel = kwargs.get("incremental_channel")
        accumulated = ""
        handle = None
        if channel is not None and hasattr(channel, "begin_incremental_reply"):
            handle = await channel.begin_incremental_reply(message.sender)
        for piece in self.pieces:
            accumulated += piece
            if handle is not None:
                await channel.update_incremental_reply(handle, accumulated)
        if self.fail:
            return RuntimeResult(
                status=RuntimeStatus.FAILED,
                context=context,
                error={"type": "RuntimeError"},
            )
        if handle is not None:
            await channel.finish_incremental_reply(handle, accumulated)
        return RuntimeResult(
            status=RuntimeStatus.COMPLETED,
            context=context,
            text=accumulated,
            agent_id="webui-agent",
            delivered_incrementally=handle is not None,
        )

    async def confirm(self, confirmation_id, context):  # pragma: no cover
        raise AssertionError("confirm must not be reached by the streaming test")


def _streaming_app(runtime: Any):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())
    container.register(AuthService, MockAuthService())
    container.register(GlobalConfig, GlobalConfig())
    container.register(WorkflowRegistry, _ChatWorkflowRegistry())
    dispatch_registry = DispatchRuleRegistry(container)
    dispatch_registry.register(
        CombinedDispatchRule(
            rule_id="webui-fallback",
            name="WebUI fallback",
            workflow_id="chat:normal",
            rule_groups=[],
        )
    )
    container.register(DispatchRuleRegistry, dispatch_registry)
    registry = AgentRegistry()
    registry.register(AgentDefinition(agent_id="webui-agent", model_priority=("model-a",)))
    registry.set_default("webui-agent")
    container.register(AgentRegistry, registry)
    container.register(AgentRuntimeExecutor, runtime)
    container.register(WorkflowDispatcher, WorkflowDispatcher(container))
    return create_web_api_app(container)


def test_the_webui_adapter_declares_the_incremental_protocol():
    """WebUI 必须实现「编辑已发出消息」的等价能力。

    在浏览器里这个能力是天然的：一条 SSE 事件就是一次改写。此前 WebUIAdapter
    不实现这个协议，于是运行时把 WebUI 归到「不能逐步显示」那一类——
    而它其实比 QQ 更有条件做到。
    """
    from kirara_ai.im.adapter import IncrementalDeliveryAdapter

    adapter = WebUIAdapter()
    assert isinstance(adapter, IncrementalDeliveryAdapter)


@pytest.mark.asyncio
async def test_the_stream_route_emits_incremental_deltas():
    runtime = _StreamingRuntime(["模拟", "回火", "算法"])
    app = _streaming_app(runtime)
    client = app.test_client()

    response = await client.post(
        "/api/llm/chat/stream",
        json={"message": "讲讲模拟回火", "session_id": "research-1"},
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")
    events = _sse_events((await response.get_data()).decode("utf-8"))
    kinds = [event["event"] for event in events]
    assert kinds[0] == "start"
    assert "delta" in kinds, f"没有任何增量事件：{kinds}"
    assert kinds[-1] == "done"

    deltas = [event["data"]["text"] for event in events if event["event"] == "delta"]
    # 增量事件送的是**新增的那一段**，不是「到目前为止的全文」：
    # 送全文会让每条事件都随回复变长，一段 8 KB 的回复要传 O(n²) 字节。
    assert "".join(deltas) == "模拟回火算法"
    done = next(event for event in events if event["event"] == "done")
    assert done["data"]["text"] == "模拟回火算法"
    assert done["data"]["status"] == "completed"
    assert done["data"]["agent_id"] == "webui-agent"
    assert done["data"]["session_id"] == "research-1"


@pytest.mark.asyncio
async def test_the_stream_route_requires_authentication():
    app = _streaming_app(_StreamingRuntime())
    client = app.test_client()

    response = await client.post(
        "/api/llm/chat/stream",
        json={"message": "hello", "session_id": "research-1"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_runtime_failure_arrives_as_an_error_event_not_a_dead_stream():
    """错误必须落在流里。

    SSE 的响应头在第一个字节之后就发出去了，此后无法再改状态码。若这时把异常
    抛出去，浏览器看到的是一个**正常结束**的空流——界面停在「正在生成」，
    而后端日志里有一条错误。用户唯一能做的判断是「它卡住了」。
    """
    runtime = _StreamingRuntime(["部分"], fail=True)
    app = _streaming_app(runtime)
    client = app.test_client()

    response = await client.post(
        "/api/llm/chat/stream",
        json={"message": "hello", "session_id": "research-1"},
        headers=AUTH,
    )

    assert response.status_code == 200
    events = _sse_events((await response.get_data()).decode("utf-8"))
    kinds = [event["event"] for event in events]
    assert "error" in kinds, f"运行时失败没有作为事件送达：{kinds}"
    assert kinds[-1] in {"error", "done"}
    error = next(event for event in events if event["event"] == "error")
    # 上游原始错误不外泄；只给一个可处置的稳定说法。
    assert error["data"]["error"]
    assert "Traceback" not in json.dumps(error["data"])


@pytest.mark.asyncio
async def test_a_rejected_request_still_answers_with_a_normal_status_code():
    """校验失败发生在流开始**之前**，那时仍该给 4xx。

    把它也塞进 SSE 会让「请求写错了」和「生成失败了」在客户端长得一样，
    而前者应当由表单校验立刻提示。
    """
    app = _streaming_app(_StreamingRuntime())
    client = app.test_client()

    response = await client.post(
        "/api/llm/chat/stream",
        json={"session_id": "research-1"},
        headers=AUTH,
    )

    assert response.status_code == 400
    assert not response.headers["Content-Type"].startswith("text/event-stream")


@pytest.mark.asyncio
async def test_the_non_streaming_route_is_unchanged():
    """需求 4 要的是「流式**和**非流式」。旧路由必须逐字节不变。"""
    runtime = _NonStreamingRuntime()
    app = _streaming_app(runtime)
    client = app.test_client()

    response = await client.post(
        "/api/llm/chat",
        json={"message": "hello", "session_id": "research-1"},
        headers=AUTH,
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["status"] == "completed"
    assert payload["session_id"] == "research-1"
    assert "text" in payload


@pytest.mark.asyncio
async def test_the_stream_route_does_not_double_deliver_the_reply():
    """增量收尾成功后不得再整段投递一次。

    否则同一段几千字的内容在界面上出现两遍——一次来自 delta 累积，
    一次来自 ``done`` 之外的兜底投递。
    """
    runtime = _StreamingRuntime(["甲", "乙"])
    app = _streaming_app(runtime)
    client = app.test_client()

    response = await client.post(
        "/api/llm/chat/stream",
        json={"message": "hello", "session_id": "research-1"},
        headers=AUTH,
    )

    events = _sse_events((await response.get_data()).decode("utf-8"))
    deltas = [event["data"]["text"] for event in events if event["event"] == "delta"]
    done = next(event for event in events if event["event"] == "done")
    assert "".join(deltas) == done["data"]["text"] == "甲乙"


@pytest.mark.asyncio
async def test_a_reply_without_any_incremental_push_still_arrives_whole():
    """运行时一次都没推增量时（例如 off 档），``done`` 仍必须带完整文本。

    否则把 ``reply_stream_mode`` 配成 ``off`` 的部署在 WebUI 上得到一个空回复,
    而后端日志显示成功。
    """
    runtime = _NonStreamingRuntime()
    app = _streaming_app(runtime)
    client = app.test_client()

    response = await client.post(
        "/api/llm/chat/stream",
        json={"message": "hello", "session_id": "research-1"},
        headers=AUTH,
    )

    events = _sse_events((await response.get_data()).decode("utf-8"))
    done = next(event for event in events if event["event"] == "done")
    assert done["data"]["text"], "没有增量时 done 事件必须补上完整文本"
