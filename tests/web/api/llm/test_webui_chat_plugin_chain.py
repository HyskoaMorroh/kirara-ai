"""前端那一个输入框必须能把五类插件全部自动带上（需求 10）。

为什么这组用例必须存在
--------------------
`tests/agent_runtime/test_persistent_resource_runtime.py` 已经证明「持久化的资源
能驱动一次真实回合」——但它是**直接调 `executor.run()`**。而前端点「发送」时走的是
`POST /api/llm/chat`，中间还隔着 HTTP 鉴权、`WebUIAdapter`、`WorkflowDispatcher`、
渠道身份解析、Agent 注册表解析。那一整段此前只有「派发到了运行时」这种用替身
（`_ChatRuntime`）做的契约测试：替身把 `options` 记下来就返回，**没有任何资源被
读过**。

于是「前端能不能完整调用 AI，并自动选中 agents / skills / mcp / hooks / prompts」
这个问题在测试里是无解的：两头都绿，中间那段没人验证。这正是本项目反复出现的
缺陷形态——每一层单独看都对。

这组用例把真实件全部接上：真的 `ResourceLifecycleService`（落盘、摘要校验）、
真的 `ResourceCatalogService`（内置目录安装）、真的 `AgentRegistry`（渠道绑定）、
真的 `AgentHookRuntime`、真的 `AgentRuntimeExecutor`、真的 `WorkflowDispatcher`、
真的 Quart 应用与鉴权。只有两处是替身，且都是**外部依赖**而非本项目逻辑：

* LLM 上游（`ControlledLLM`）——不打真实模型；
* MCP 传输层（`StubMCPManager`）——`context7` 要 `npx` 拉进程，而这条用例问的是
  「工具有没有被广告给模型、调用结果有没有回到对话里」，不是「npx 能不能装」。
  真进程那一路由 `test_persistent_resource_runtime.py` 的 integration 用例覆盖。

**请求体里一个插件名都不出现。** 这是「自动选择」的判据：前端只发一句话和
`session_id`，五类插件必须靠「渠道身份 → Agent → 绑定」自己被选中。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentHookRuntime,
    AgentRegistry,
    AgentRuntimeExecutor,
    ChannelContext,
)
from kirara_ai.agent_runtime.session_store import SessionStore
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.format.tool import Function, ToolCall
from kirara_ai.llm.resilience import ChatExecutionResult
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context
from kirara_ai.web.auth.services import AuthService, MockAuthService
from kirara_ai.workflow.core.dispatch.registry import DispatchRuleRegistry
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry

AUTH = {"Authorization": "Bearer mock_token"}

#: 前端发的就是这两个字段。**刻意不带** agent_id / 任何资源 ID：
#: 「自动选择」的意思就是请求方不需要知道这些。
FRONTEND_PAYLOAD = {"message": "帮我查一下 pytest 的 fixture 怎么用", "session_id": "researcher-1"}


class ControlledLLM:
    """记录每一次请求并按脚本回复。上游是外部依赖，不打真实模型。"""

    def __init__(self, responses: list[LLMChatResponse]) -> None:
        self.responses = list(responses)
        self.requests: list = []

    def execute_chat(self, request, **_options):
        self.requests.append(request)
        return ChatExecutionResult(
            response=self.responses.pop(0),
            trace_id=f"webui-e2e-{len(self.requests)}",
            attempts=[],
        )


class StubMCPManager:
    """只替换传输层：工具目录与调用结果是真的经过运行时的策略与审计的。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.audit_records: list[dict] = []
        self._tools = {
            "resolve-library-id": SimpleNamespace(
                server_id="context7",
                original_name="resolve-library-id",
                tool_info=SimpleNamespace(
                    name="resolve-library-id",
                    description="Resolve a library name to a documentation ID",
                    inputSchema={
                        "type": "object",
                        "properties": {"libraryName": {"type": "string"}},
                        "required": ["libraryName"],
                    },
                ),
            )
        }

    def get_tools(self):
        return self._tools

    async def call_tool(self, name, args, **options):
        self.calls.append((name, dict(args)))
        return SimpleNamespace(
            content=[SimpleNamespace(text="/pytest-dev/pytest fixtures 文档")],
            isError=False,
        )


def _tool_call() -> ToolCall:
    return ToolCall(
        id="webui-e2e-call",
        type="function",
        function=Function(
            name="resolve-library-id", arguments={"libraryName": "pytest"}
        ),
    )


def _scripted_replies() -> list[LLMChatResponse]:
    """第一轮要工具，第二轮给最终回答——两轮才验证得到工具结果回到了对话里。"""
    return [
        LLMChatResponse(
            model="chat-model",
            message=Message(role="assistant", content=[], tool_calls=[_tool_call()]),
        ),
        LLMChatResponse(
            model="chat-model",
            message=Message(
                role="assistant",
                content=[LLMChatTextContent(text="pytest 的 fixture 用法已查到")],
            ),
        ),
    ]


@pytest.fixture
def webui_stack(tmp_path: Path):
    """真实装配：目录安装五类资源、绑定到一个 Agent、接上 WebUI 渠道。"""

    data_root = tmp_path / "vps-data"
    lifecycle = ResourceLifecycleService(data_root)
    catalog = ResourceCatalogService(lifecycle)

    # 走真实的内置目录安装，而不是手搓 archive：需求 10 说的「首屏就有货」
    # 指的就是这批条目，而它们的落盘形状（摘要、权限、entry 名）决定后面
    # `read_entry` 能不能通过校验。
    for catalog_id in (
        "prompt:office-research",
        "memory:research-context",
        "mcp:context7",
        "hook:ai-debug",
    ):
        catalog.install(catalog_id)
    # skill 用一条本地内容（真实的 agent-browser 要出网到 GitHub，
    # 而这条用例问的是「Skill 正文有没有进 system 消息」）。
    lifecycle.author_document(
        resource_id="prompt.local-skill-note",
        resource_type="prompt",
        content="占位：这条只用来确认未绑定的资源不会被带上。\n",
    )

    for resource_id in (
        "prompt.office-research",
        "memory.research-context",
        "mcp.context7",
        "hook.ai-debug",
    ):
        lifecycle.enable(resource_id, confirmed=True)

    def binding(resource_id: str, resource_type: str):
        return lifecycle.resolve_binding(
            resource_id,
            resource_type,
            version=lifecycle.get_resource(resource_id)["current_version"],
            enabled=True,
        )

    agent = AgentDefinition(
        agent_id="webui-research-agent",
        model_priority=("chat-model",),
        capabilities=frozenset({"research", "process.execute"}),
        prompt_bindings=(binding("prompt.office-research", "prompt"),),
        memory_bindings=(binding("memory.research-context", "memory"),),
        mcp_bindings=(binding("mcp.context7", "mcp"),),
        hook_bindings=(binding("hook.ai-debug", "hook"),),
        mcp_allowlist=frozenset({"context7.resolve-library-id"}),
        max_tool_iterations=2,
    )
    registry = AgentRegistry(data_root)
    # **必须在创建者身份下注册**：`_bind_creation_owner` 会把当前 principal 的
    # subject 盖到 `owner_subject` 上，而 MCP 工具广告与命令型 Hook 都要求
    # `principal_can_control_agent(agent.owner_subject)`。
    #
    # 生产里这一步天然满足——Agent 是通过 HTTP 接口创建的，而那条路径跑在
    # `runtime_principal_context` 里面。测试里在上下文外注册会得到
    # `owner_subject=None`，于是这一轮拿到零个工具、命令 Hook 被拒——
    # 那不是缺陷，那正是需求 10 的「只有创建者能通过插件操作服务器」在生效
    # （见 `test_a_foreign_agent_gets_no_tools_or_command_hooks`）。
    #
    # subject 与 `MockAuthService` 默认签发的一致，否则 HTTP 请求带的身份
    # 与 Agent 的属主对不上，仍然会被拒。
    with runtime_principal_context(
        RuntimePrincipal(subject="mock-subject", is_creator=True)
    ):
        registry.register(agent)
        # 只绑渠道，不设默认：这样「解析到它」只能是靠 webui 这个渠道身份，
        # 而不是靠「碰巧是唯一的默认 Agent」。
        registry.configure(agent, channels=("webui",))

    audit: list[dict] = []
    llm = ControlledLLM(_scripted_replies())
    mcp = StubMCPManager()
    hook_runtime = AgentHookRuntime(
        resource_service=lifecycle,
        audit_sink=audit.append,
    )
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=mcp,
        resource_service=lifecycle,
        session_store=SessionStore(data_root),
        hook_runtime=hook_runtime,
        audit_sink=audit.append,
    )

    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())
    container.register(AuthService, MockAuthService(creator=True))
    container.register(GlobalConfig, GlobalConfig())
    container.register(WorkflowRegistry, WorkflowRegistry(container))
    # 一条规则都不注册：WebUI 入口按渠道身份路由，不依赖遗留工作流规则。
    container.register(DispatchRuleRegistry, DispatchRuleRegistry(container))
    container.register(AgentRegistry, registry)
    container.register(AgentRuntimeExecutor, executor)
    container.register(ResourceLifecycleService, lifecycle)

    from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher

    container.register(WorkflowDispatcher, WorkflowDispatcher(container))

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return SimpleNamespace(
        client=app.test_client(),
        lifecycle=lifecycle,
        llm=llm,
        mcp=mcp,
        audit=audit,
        agent=agent,
    )


async def _chat(stack, payload: dict | None = None):
    response = await stack.client.post(
        "/api/llm/chat", json=payload or FRONTEND_PAYLOAD, headers=AUTH
    )
    body = await response.get_json()
    return response, body


@pytest.mark.asyncio
async def test_the_frontend_gets_a_reply_without_naming_any_plugin(webui_stack):
    """整条链路的基线：一句话进去，一段回复出来。"""
    response, body = await _chat(webui_stack)

    assert response.status_code == 200, body
    assert body["status"] == "completed"
    assert body["text"] == "pytest 的 fixture 用法已查到"
    # Agent 是解析出来的，不是请求里指定的。
    assert body["agent_id"] == "webui-research-agent"
    assert "agent_id" not in FRONTEND_PAYLOAD


@pytest.mark.asyncio
async def test_the_bound_prompt_reaches_the_model(webui_stack):
    """提示词必须进 system 消息。

    这是「自动选中 prompts」的唯一可验证判据：装了、启用了、绑定了，
    都不等于它到了模型面前。
    """
    await _chat(webui_stack)

    system = webui_stack.llm.requests[0].messages[0]
    assert system.role == "system"
    text = "".join(part.text for part in system.content)
    # 内置提示词的原文（需求 10 点名的那一段）。
    assert "我是上班族" in text


@pytest.mark.asyncio
async def test_the_bound_memory_policy_reaches_the_model(webui_stack):
    """记忆策略同样只在 system 里能验证得到。"""
    await _chat(webui_stack)

    text = "".join(
        part.text for part in webui_stack.llm.requests[0].messages[0].content
    )
    assert "研究型 Agent" in text


@pytest.mark.asyncio
async def test_the_bound_mcp_tool_is_advertised_to_the_model(webui_stack):
    """MCP 工具要出现在**第一轮**请求的 tools 里。

    出现在第二轮不算：模型是靠第一轮的工具清单决定要不要调用的。
    """
    await _chat(webui_stack)

    first = webui_stack.llm.requests[0]
    assert first.tools, "第一轮就该带上工具清单"
    assert "resolve-library-id" in {tool.name for tool in first.tools}


@pytest.mark.asyncio
async def test_the_tool_result_comes_back_into_the_conversation(webui_stack):
    """工具真的被调用，且结果回到了第二轮的对话里。

    只断言「工具被广告了」不够——广告出去而调用链断掉时，模型会拿不到结果
    却照样给出一个自信的回答。
    """
    await _chat(webui_stack)

    assert webui_stack.mcp.calls == [("resolve-library-id", {"libraryName": "pytest"})]
    tool_messages = [
        message
        for message in webui_stack.llm.requests[1].messages
        if message.role == "tool"
    ]
    assert tool_messages, "第二轮必须带上工具结果"
    assert "pytest" in str(tool_messages[-1].content).lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_bound_hooks_fire_across_the_whole_turn(webui_stack):
    """Hook 要在这一轮的各个阶段真的触发。

    绑定了 hook 而事件没触发，症状是「审计里什么都没有」——
    而那与「没绑 hook」在界面上无法区分。

    标成 integration：内置 `hook:ai-debug` 的五个事件是**命令型**声明，
    每次触发都真的起一个 `{python} -m kirara_ai.agent_runtime.audit_hook_command`
    子进程（`timeout_ms` 5000）。这一条因此比同文件其他用例慢一个量级，
    且在整机负载高时会撞上那个超时——同一份代码单独跑稳定通过。
    其余十条用例不依赖子进程，留在默认集合里。
    """
    await _chat(webui_stack)

    fired = {
        item["event"] for item in webui_stack.audit if item.get("outcome") == "success"
    }
    # 一轮完整对话应当覆盖：会话开始、用户输入、工具前后、结束。
    assert {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"} <= fired


@pytest.mark.asyncio
async def test_an_unbound_resource_is_not_carried_along(webui_stack):
    """没绑定的资源不能进请求。

    「全都带上」与「按绑定选中」在成功路径上看起来一样，
    区别只在这一条：多带的那些会挤占上下文、并让一次编辑影响到无关的 Agent。
    """
    await _chat(webui_stack)

    text = "".join(
        part.text for part in webui_stack.llm.requests[0].messages[0].content
    )
    assert "占位" not in text


@pytest.mark.asyncio
async def test_the_session_is_persisted_under_the_data_root(webui_stack):
    """这一轮的历史要落到 VPS 数据目录里，重启后还在。"""
    await _chat(webui_stack)

    sessions = list((webui_stack.lifecycle.data_path / "sessions").glob("*.json"))
    assert sessions, "会话没有落盘"
    stored = json.loads(sessions[0].read_text(encoding="utf-8"))
    assert stored, "会话文件是空的"


@pytest.mark.asyncio
async def test_the_reply_is_bound_to_the_webui_channel_identity(webui_stack):
    """`session_key` 必须由渠道身份构成，而不是请求里的随便一个字符串。

    需求 10 要的关系模型是「渠道身份 → Agent → 上游/备用链 → 插件」。
    `session_key` 是这条链的第一环，它错了，历史与记忆都会落到别人的桶里。
    """
    _, body = await _chat(webui_stack)

    assert body["session_key"].startswith("webui/webui/webui/")
    assert body["session_key"].endswith("researcher-1")


@pytest.mark.asyncio
async def test_a_second_turn_reuses_the_same_agent_and_session(webui_stack):
    """同一个 `session_id` 的第二轮要落在同一个 Agent 与同一段历史上。"""
    webui_stack.llm.responses.extend(_scripted_replies())

    _, first = await _chat(webui_stack)
    _, second = await _chat(webui_stack)

    assert second["agent_id"] == first["agent_id"]
    assert second["session_key"] == first["session_key"]


@pytest.mark.asyncio
async def test_a_foreign_agent_gets_no_tools_or_command_hooks(tmp_path: Path):
    """非创建者的请求仍然得到回复，但拿不到能操作服务器的插件（需求 10）。

    需求 10 的原文：「只有该项目的创建者才能通过前述 skills、hooks、MCP、
    Prompts、agents 等插件通过 AI 修改服务器 VPS 里边的内容或者在 VPS 中执行文件
    操作等指令，其他使用者收到涉及修改部署 VPS 里边的内容或在 VPS 中进行文件操作
    等命令一律忽视，**但是仍然会进行正常的 AI 结合前述插件处理后的回复效果**。」

    所以这条用例要同时钉住两件事，缺一不可：

    1. **仍然有回复**，且提示词与记忆策略照常生效——把非创建者一律 403
       就违反了「仍然会进行正常的 AI 结合插件处理后的回复」；
    2. **拿不到工具**（MCP 工具能读写服务器）**也不跑命令型 Hook**（起进程）。

    这不是构造出来的场景：`owner_subject` 与当前 principal 不匹配时正是这个结果。
    发现它的过程恰好相反——上面那些用例最初在上下文外注册 Agent，
    于是全部拿到零个工具，而那时看起来像缺陷。
    """
    data_root = tmp_path / "vps-data"
    lifecycle = ResourceLifecycleService(data_root)
    catalog = ResourceCatalogService(lifecycle)
    for catalog_id in ("prompt:office-research", "mcp:context7", "hook:ai-debug"):
        catalog.install(catalog_id)
    for resource_id in ("prompt.office-research", "mcp.context7", "hook.ai-debug"):
        lifecycle.enable(resource_id, confirmed=True)

    def binding(resource_id: str, resource_type: str):
        return lifecycle.resolve_binding(
            resource_id,
            resource_type,
            version=lifecycle.get_resource(resource_id)["current_version"],
            enabled=True,
        )

    agent = AgentDefinition(
        agent_id="someone-elses-agent",
        model_priority=("chat-model",),
        capabilities=frozenset({"research", "process.execute"}),
        prompt_bindings=(binding("prompt.office-research", "prompt"),),
        mcp_bindings=(binding("mcp.context7", "mcp"),),
        hook_bindings=(binding("hook.ai-debug", "hook"),),
        mcp_allowlist=frozenset({"context7.resolve-library-id"}),
        max_tool_iterations=2,
    )
    registry = AgentRegistry(data_root)
    # 属主是**另一个人**：请求带的是 `mock-subject`（`MockAuthService` 默认），
    # 这里故意用别的 subject 注册。
    with runtime_principal_context(
        RuntimePrincipal(subject="the-real-creator", is_creator=True)
    ):
        registry.register(agent)
        registry.configure(agent, channels=("webui",))

    audit: list[dict] = []
    # 只有一条回复：拿不到工具时模型不会发起工具调用，因此这一轮只请求一次。
    llm = ControlledLLM(
        [
            LLMChatResponse(
                model="chat-model",
                message=Message(
                    role="assistant",
                    content=[LLMChatTextContent(text="我可以说明 fixture 的用法")],
                ),
            )
        ]
    )
    mcp = StubMCPManager()
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=mcp,
        resource_service=lifecycle,
        session_store=SessionStore(data_root),
        hook_runtime=AgentHookRuntime(
            resource_service=lifecycle, audit_sink=audit.append
        ),
        audit_sink=audit.append,
    )

    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())
    container.register(AuthService, MockAuthService(creator=True))
    container.register(GlobalConfig, GlobalConfig())
    container.register(WorkflowRegistry, WorkflowRegistry(container))
    container.register(DispatchRuleRegistry, DispatchRuleRegistry(container))
    container.register(AgentRegistry, registry)
    container.register(AgentRuntimeExecutor, executor)
    container.register(ResourceLifecycleService, lifecycle)

    from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher

    container.register(WorkflowDispatcher, WorkflowDispatcher(container))
    app = create_web_api_app(container)
    app.config["TESTING"] = True

    response = await app.test_client().post(
        "/api/llm/chat", json=FRONTEND_PAYLOAD, headers=AUTH
    )
    body = await response.get_json()

    # 1. 仍然有正常回复，提示词照常生效。
    assert response.status_code == 200, body
    assert body["text"] == "我可以说明 fixture 的用法"
    system = "".join(part.text for part in llm.requests[0].messages[0].content)
    assert "我是上班族" in system, "提示词对非创建者也应生效"

    # 2. 但一个工具都没有，且没有任何工具被调用过。
    assert not llm.requests[0].tools, "非属主不该拿到能操作服务器的工具"
    assert mcp.calls == []
