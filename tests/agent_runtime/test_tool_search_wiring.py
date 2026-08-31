"""Tool Search 必须真的接进请求装配，不能是一个「有定义、零调用」的模块。

本项目已经吃过那个亏两次：`UsageSource.ESTIMATED` 曾经有定义、有测试、
主链路零调用；`reset_provider_circuit` 曾经实现完整但没有任何路由。
两次都表现为「功能在文档里存在，在部署里不存在」。

因此这一组用例走**完整的一轮对话**：构造一个真的有几十个 MCP 工具的运行时，
断言发给模型的请求里究竟带了什么。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentRegistry,
    AgentRuntimeExecutor,
    ChannelContext,
    ResourceBinding,
    RuntimeStatus,
)
from kirara_ai.agent_runtime.tool_search import TOOL_SEARCH_TOOL_NAME
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.format.tool import Function, ToolCall
from kirara_ai.llm.resilience import ChatExecutionResult
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context

CREATOR = RuntimePrincipal(subject="tool-search-creator", is_creator=True)


@pytest.fixture
def creator_principal():
    with runtime_principal_context(CREATOR):
        yield


class _RecordingLLM:
    """记录每一次请求带了哪些工具，并按脚本回答。"""

    def __init__(self, responses: list[LLMChatResponse]):
        self._responses = list(responses)
        self.tool_names_per_request: list[list[str]] = []
        self.system_texts: list[str] = []

    def execute_chat(self, request, **_options):
        self.tool_names_per_request.append(
            [tool.name for tool in (request.tools or [])]
        )
        self.system_texts.append(
            "\n".join(
                part.text
                for message in request.messages
                if message.role == "system"
                for part in message.content
                if isinstance(part, LLMChatTextContent)
            )
        )
        response = self._responses.pop(0)
        return ChatExecutionResult(response=response, trace_id="t", attempts=[])


class _ManyToolsMCP:
    """一个提供 40 个工具的假 MCP 管理器（三个服务器的常见规模）。"""

    def __init__(self, count: int = 40):
        self.count = count
        self.calls: list[tuple[str, dict]] = []

    def get_tools(self) -> dict:
        # 条目要带 `server_id` 与 `original_name`：白名单解析用它们把持久化的
        # `server.tool` 身份对上当前缓存名，缺了就一个也解析不出来。
        return {
            f"srv.tool_{index}": SimpleNamespace(
                server_id="srv",
                original_name=f"tool_{index}",
                tool_info=SimpleNamespace(
                    name=f"tool_{index}",
                    description=(
                        "读取一个文件" if index == 7 else f"第 {index} 个工具的用途"
                    ),
                    inputSchema={
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                ),
            )
            for index in range(self.count)
        }

    def requires_confirmation(self, _name: str) -> bool:
        return False

    async def call_tool(self, name: str, arguments: dict, **_policy):
        # 真实 `call_tool` 还带一批策略 kwargs（agent_allowlist、
        # agent_mcp_server_ids、confirmed 等）。用 **_policy 吸收它们，
        # 否则签名不匹配会让调用静默走进异常分支，看起来像「工具没被调用」。
        self.calls.append((name, arguments))
        return {"ok": True}


def _text_reply(text: str) -> LLMChatResponse:
    return LLMChatResponse(
        model="m",
        message=Message(role="assistant", content=[LLMChatTextContent(text=text)]),
    )


def _tool_reply(name: str, arguments: dict) -> LLMChatResponse:
    return LLMChatResponse(
        model="m",
        message=Message(
            role="assistant",
            content=[],
            tool_calls=[
                ToolCall(
                    id="c1",
                    type="function",
                    function=Function(name=name, arguments=arguments),
                )
            ],
        ),
    )


def _context() -> ChannelContext:
    return ChannelContext(
        channel_type="webui",
        adapter_instance="test",
        account_scope="acct",
        conversation_scope="c2c:user",
        sender_scope="user",
    )


def _message(text: str = "帮我读一个文件") -> IMMessage:
    return IMMessage(ChatSender.from_c2c_chat("user", "Tester"), [TextMessage(text)])


HASH_MCP = "d" * 64


def _mcp_binding() -> ResourceBinding:
    """把 `srv` 这个服务器绑定到 Agent。

    白名单解析要求工具属于 Agent 绑定的服务器集合——那是「工具白名单」这条边界的
    一半，少了它任何名字都解析不出来。
    """
    return ResourceBinding(
        resource_id="mcp.srv",
        resource_type="mcp",
        version="1.0.0",
        content_sha256=HASH_MCP,
    )


def _runtime(tmp_path, llm, mcp, *, threshold: int | None = None, tool_count: int = 40):
    registry = AgentRegistry(tmp_path / "agents")
    agent = AgentDefinition(
        agent_id="a",
        model_priority=("m",),
        mcp_bindings=(_mcp_binding(),),
        mcp_allowlist=frozenset(f"srv.tool_{index}" for index in range(tool_count)),
        max_tool_iterations=3,
    )
    registry.register(agent)
    registry.set_default(agent.agent_id)
    kwargs = {}
    if threshold is not None:
        kwargs["tool_search_threshold"] = threshold
    return AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=mcp,
        resource_loader=lambda _resource_id: "",
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_many_tools_are_replaced_by_a_catalog_plus_one_search_tool(tmp_path):
    llm = _RecordingLLM([_text_reply("好的")])
    mcp = _ManyToolsMCP(40)
    runtime = _runtime(tmp_path, llm, mcp, threshold=12)

    result = await runtime.run(_context(), _message())

    assert result.status is RuntimeStatus.COMPLETED
    sent = llm.tool_names_per_request[0]
    # 回归点：修之前这里是 40 个工具的完整 schema。
    assert TOOL_SEARCH_TOOL_NAME in sent
    assert len(sent) == 1
    # 目录进系统提示词：模型要知道有哪些工具，才可能去搜。
    assert "srv.tool_7" in llm.system_texts[0]
    assert "读取一个文件" in llm.system_texts[0]


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_a_small_tool_set_is_still_injected_in_full(tmp_path):
    llm = _RecordingLLM([_text_reply("好的")])
    mcp = _ManyToolsMCP(3)
    runtime = _runtime(tmp_path, llm, mcp, threshold=12, tool_count=3)

    await runtime.run(_context(), _message())

    sent = llm.tool_names_per_request[0]
    # 给三个工具再加一层搜索只是凭空多一轮往返。
    assert TOOL_SEARCH_TOOL_NAME not in sent
    assert set(sent) == {"srv.tool_0", "srv.tool_1", "srv.tool_2"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_searching_returns_full_definitions_and_the_next_request_can_call_them(
    tmp_path,
):
    llm = _RecordingLLM(
        [
            _tool_reply(TOOL_SEARCH_TOOL_NAME, {"query": "读取"}),
            _tool_reply("srv.tool_7", {"path": "/tmp/x"}),
            _text_reply("已经读好了"),
        ]
    )
    mcp = _ManyToolsMCP(40)
    runtime = _runtime(tmp_path, llm, mcp, threshold=12)

    result = await runtime.run(_context(), _message())

    assert result.status is RuntimeStatus.COMPLETED
    # 搜到之后那个工具必须**真的可调用**：只把名字告诉模型、不放进工具列表，
    # 模型下一轮调用它会被判成「未授权的工具」。
    assert "srv.tool_7" in llm.tool_names_per_request[1]
    assert ("srv.tool_7", {"path": "/tmp/x"}) in mcp.calls


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_a_search_with_no_match_does_not_break_the_turn(tmp_path):
    llm = _RecordingLLM(
        [
            _tool_reply(TOOL_SEARCH_TOOL_NAME, {"query": "quantum_teleport"}),
            _text_reply("没有找到合适的工具"),
        ]
    )
    mcp = _ManyToolsMCP(40)
    runtime = _runtime(tmp_path, llm, mcp, threshold=12)

    result = await runtime.run(_context(), _message())

    # 无命中是一个正常结果，不是错误：模型据此改用别的办法回答。
    assert result.status is RuntimeStatus.COMPLETED
    assert mcp.calls == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_the_search_tool_stays_available_after_a_search(tmp_path):
    llm = _RecordingLLM(
        [
            _tool_reply(TOOL_SEARCH_TOOL_NAME, {"query": "tool_1"}),
            _tool_reply(TOOL_SEARCH_TOOL_NAME, {"query": "tool_2"}),
            _text_reply("好"),
        ]
    )
    mcp = _ManyToolsMCP(40)
    runtime = _runtime(tmp_path, llm, mcp, threshold=12)

    await runtime.run(_context(), _message())

    # 搜一次就把搜索工具收走，会让模型在第一次没搜准之后无路可走。
    assert TOOL_SEARCH_TOOL_NAME in llm.tool_names_per_request[1]


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_threshold_zero_keeps_the_previous_full_injection_behaviour(tmp_path):
    llm = _RecordingLLM([_text_reply("好")])
    mcp = _ManyToolsMCP(40)
    runtime = _runtime(tmp_path, llm, mcp, threshold=0)

    await runtime.run(_context(), _message())

    # 0 是关闭开关。一个想关掉这个特性的部署必须能拿回逐字节一致的旧行为。
    sent = llm.tool_names_per_request[0]
    assert TOOL_SEARCH_TOOL_NAME not in sent
    assert len(sent) == 40


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_search_never_exposes_a_tool_outside_the_allowlist(tmp_path):
    llm = _RecordingLLM(
        [
            _tool_reply(TOOL_SEARCH_TOOL_NAME, {"query": ""}),
            _text_reply("好"),
        ]
    )
    mcp = _ManyToolsMCP(40)
    registry = AgentRegistry(tmp_path / "agents")
    # 只允许两个工具，而 MCP 侧有 40 个。
    agent = AgentDefinition(
        agent_id="a",
        model_priority=("m",),
        mcp_bindings=(_mcp_binding(),),
        mcp_allowlist=frozenset({"srv.tool_0", "srv.tool_1"}),
        max_tool_iterations=3,
    )
    registry.register(agent)
    registry.set_default(agent.agent_id)
    runtime = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=mcp,
        resource_loader=lambda _resource_id: "",
        tool_search_threshold=1,
    )

    await runtime.run(_context(), _message())

    # 搜索是一条**取回**路径，不是一条提权路径。白名单之外的工具既不能出现在
    # 目录里，也不能被搜出来——否则「工具白名单」这个边界就没了。
    catalog = llm.system_texts[0]
    assert "srv.tool_5" not in catalog
    for names in llm.tool_names_per_request:
        assert "srv.tool_5" not in names
