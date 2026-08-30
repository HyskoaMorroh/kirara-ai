"""技能正文里的命令若在服务器上没装，必须在广告与正文里说清（需求 10）。

需求 10 要的是「下载安装在 VPS 里边的各种插件**起作用**」，并点名 agent-browser。
它的 SKILL.md 通篇是 `agent-browser ...` 命令，而这个 CLI 是否真的装在 VPS 上，
由 `SystemDependencyService` 记录（`agent-browser-cli` / `agent-browser-browser`）。

此前这两件事**互不知情**：`ResourceCatalogService.project_dependencies` 只把依赖
状态投影给**安装界面**看，而 Agent 运行时完全不读它。于是没装 CLI 时会发生这样一轮：

1. 模型读到技能，照着写出 `agent-browser open ...`；
2. 命令在服务器上不存在；
3. 模型无从得知，只能把「我已经打开了浏览器」当成事实继续往下答。

这是最坏的一类失败：**没有报错，只有一个自信的假答案**。用户看不出区别，
因为模型的语气和真的执行成功时一模一样。

因此技能广告与正文都要带一句就绪状态：

- 依赖已就绪 → 不加噪音，一个字都不加；
- 依赖缺失 → 明确说明哪个命令不可用，并要求模型不要假装执行过；
- 拿不到依赖服务（未接线、探测异常）→ 什么都不说。「不知道」不能冒充
  「已就绪」，也不该冒充「缺失」——后者会让一次本来能用的技能被无谓地劝退。
"""

from __future__ import annotations

import pytest

from kirara_ai.agent_runtime.skills import (
    SKILL_TOOL_PREFIX,
    build_skill_tools,
    skill_advertisement,
    skill_catalog_section,
    skill_readiness_note,
)

AGENT_BROWSER_SKILL = """---
name: agent-browser
description: Browser automation CLI for AI agents
---

Run `agent-browser skills get core` first.
"""


class _Dependencies:
    """依赖服务替身：只实现运行时真正会调用的那一个方法。"""

    def __init__(self, states: dict[str, bool | None], *, raises: bool = False):
        self.states = states
        self.raises = raises
        self.calls: list[str] = []

    def get_dependency(self, dependency_id: str) -> dict:
        self.calls.append(dependency_id)
        if self.raises:
            raise RuntimeError("probe backend unavailable")
        if dependency_id not in self.states:
            raise KeyError(dependency_id)
        ready = self.states[dependency_id]
        return {
            "dependency_id": dependency_id,
            "name": dependency_id,
            "ready": ready,
            "status": "ready" if ready else "missing",
        }


class TestReadinessNote:
    def test_a_ready_dependency_adds_nothing(self):
        """已就绪时一个字都不加：每一句多余的话都是每轮都要付费的噪音。"""
        note = skill_readiness_note(
            ["agent-browser-cli"], _Dependencies({"agent-browser-cli": True})
        )

        assert note == ""

    def test_a_missing_dependency_is_named(self):
        note = skill_readiness_note(
            ["agent-browser-cli"], _Dependencies({"agent-browser-cli": False})
        )

        assert "agent-browser-cli" in note

    def test_a_missing_dependency_forbids_pretending(self):
        """不写这句，模型会把「我已经执行了」当事实继续答——没有报错的假答案。"""
        note = skill_readiness_note(
            ["agent-browser-cli"], _Dependencies({"agent-browser-cli": False})
        )

        assert "不要" in note or "不得" in note

    def test_only_the_missing_ones_are_named(self):
        note = skill_readiness_note(
            ["agent-browser-cli", "agent-browser-browser"],
            _Dependencies(
                {"agent-browser-cli": True, "agent-browser-browser": False}
            ),
        )

        assert "agent-browser-browser" in note
        # 已装好的那个不必出现：它不是问题，提它只会稀释真正的警告。
        assert "agent-browser-cli" not in note.replace("agent-browser-browser", "")

    def test_no_dependencies_means_no_note(self):
        assert skill_readiness_note([], _Dependencies({})) == ""

    def test_an_unavailable_dependency_service_says_nothing(self):
        """「不知道」不能冒充「缺失」：那会劝退一个本来能用的技能。"""
        assert skill_readiness_note(["agent-browser-cli"], None) == ""

    def test_a_raising_dependency_service_says_nothing(self):
        """探测后端异常不该让一次正常提问失败，也不该编一个状态出来。"""
        note = skill_readiness_note(
            ["agent-browser-cli"],
            _Dependencies({"agent-browser-cli": True}, raises=True),
        )

        assert note == ""

    def test_an_unknown_dependency_id_says_nothing(self):
        """依赖表里没有登记这个 id：我们不知道它的状态，不能断言缺失。"""
        note = skill_readiness_note(["not-registered"], _Dependencies({}))

        assert note == ""

    def test_a_null_ready_state_is_unknown_not_missing(self):
        """`ready` 为 None 是「还没探测过」，与「探测过、不在」不同。"""
        note = skill_readiness_note(
            ["agent-browser-cli"], _Dependencies({"agent-browser-cli": None})
        )

        assert note == ""


class TestAdvertisementCarriesReadiness:
    def test_the_advertisement_warns_when_a_dependency_is_missing(self):
        advertisement = skill_advertisement(
            "skill.ab",
            AGENT_BROWSER_SKILL,
            readiness_note="服务器上缺少命令：agent-browser-cli。",
        )

        assert advertisement is not None
        assert "agent-browser-cli" in advertisement["readiness_note"]

    def test_the_tool_description_carries_the_warning(self):
        """警告必须在**工具描述**里，那是模型决定是否调用时唯一会读的地方。"""
        tools = build_skill_tools(
            [
                {
                    "resource_id": "skill.ab",
                    "name": "agent-browser",
                    "description": "Browser automation",
                    "readiness_note": "服务器上缺少命令：agent-browser-cli。",
                }
            ]
        )

        assert "agent-browser-cli" in tools[0].description

    def test_the_catalog_line_carries_the_warning(self):
        section = skill_catalog_section(
            [
                {
                    "resource_id": "skill.ab",
                    "name": "agent-browser",
                    "description": "Browser automation",
                    "readiness_note": "服务器上缺少命令：agent-browser-cli。",
                }
            ]
        )

        assert "agent-browser-cli" in section

    def test_a_ready_skill_reads_exactly_as_before(self):
        """就绪时输出必须与没有这个特性时逐字节一致，不引入噪音。"""
        with_note = build_skill_tools(
            [
                {
                    "resource_id": "skill.ab",
                    "name": "agent-browser",
                    "description": "Browser automation",
                    "readiness_note": "",
                }
            ]
        )[0].description
        without_key = build_skill_tools(
            [
                {
                    "resource_id": "skill.ab",
                    "name": "agent-browser",
                    "description": "Browser automation",
                }
            ]
        )[0].description

        assert with_note == without_key
        assert SKILL_TOOL_PREFIX  # 前缀常量仍然导出，工具名契约未变


class _FakeResourceService:
    """只实现运行时会用到的两个方法。"""

    def __init__(self, resources: dict[str, dict]):
        self.resources = resources

    def get_resource(self, resource_id: str) -> dict:
        return self.resources[resource_id]

    def resolve_binding(
        self,
        resource_id,
        resource_type,
        *,
        version=None,
        enabled=True,
        version_policy="fixed",
    ):
        from kirara_ai.agent_runtime.core import ResourceBinding

        return ResourceBinding(
            resource_id=resource_id,
            resource_type=resource_type,
            version=version or "1.0.0",
            content_sha256="a" * 64,
            enabled=enabled,
            version_policy=version_policy,
        )


class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, request, **_options):
        from kirara_ai.llm.resilience import ChatExecutionResult

        self.requests.append(request)
        return ChatExecutionResult(
            response=self.responses.pop(0),
            trace_id=f"trace-{len(self.requests)}",
            attempts=[],
        )


class _NoMCP:
    def get_tools(self):
        return {}

    def get_server(self, _server_id):
        return None


@pytest.mark.asyncio
async def test_a_missing_dependency_reaches_the_model_in_the_tool_description():
    """端到端：没装 CLI 时，警告必须真的出现在发给模型的请求里。

    模块级用例只证明了函数会产出这句话。函数产出而运行时不传，
    正是本轮反复在修的那类缺陷——有实现、有测试、主链路零调用。
    """
    from kirara_ai.agent_runtime.core import (
        AgentDefinition,
        AgentRegistry,
        ChannelContext,
        ResourceBinding,
    )
    from kirara_ai.agent_runtime.executor import AgentRuntimeExecutor, RuntimeStatus
    from kirara_ai.im.message import IMMessage, TextMessage
    from kirara_ai.im.sender import ChatSender
    from kirara_ai.llm.format.message import LLMChatTextContent
    from kirara_ai.llm.format.response import LLMChatResponse, Message

    resource_id = "skill.agent-browser"
    resources = {
        resource_id: {
            "resource_id": resource_id,
            "type": "skill",
            "name": "agent-browser",
            "current_version": "1.0.0",
        }
    }
    agent = AgentDefinition(
        agent_id="readiness-agent",
        model_priority=("m1",),
        skill_bindings=(
            ResourceBinding(
                resource_id=resource_id,
                resource_type="skill",
                version="1.0.0",
                content_sha256="a" * 64,
            ),
        ),
    )
    registry = AgentRegistry()
    registry.register(agent)
    registry.set_default(agent.agent_id)

    llm = _FakeLLM(
        [
            LLMChatResponse(
                model="m1",
                message=Message(
                    role="assistant", content=[LLMChatTextContent(text="done")]
                ),
            )
        ]
    )
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=_NoMCP(),
        resource_loader=lambda _rid, _version: AGENT_BROWSER_SKILL,
        resource_service=_FakeResourceService(resources),
        dependency_service=_Dependencies(
            {"agent-browser-cli": False, "agent-browser-browser": True}
        ),
    )

    result = await executor.run(
        ChannelContext(
            channel_type="telegram",
            adapter_instance="telegram-main",
            account_scope="main",
            conversation_scope="c2c:user",
            sender_scope="user",
        ),
        IMMessage(
            sender=ChatSender.from_c2c_chat("user", "User"),
            message_elements=[TextMessage("open a browser")],
        ),
    )

    assert result.status is RuntimeStatus.COMPLETED
    request = llm.requests[0]
    tool = next(
        item for item in (request.tools or []) if item.name == f"skill_{resource_id}"
    )
    # 缺失的那个被点名；已就绪的那个不出现（提它只会稀释真正的警告）。
    assert "agent-browser-cli" in tool.description
    assert "不要" in tool.description
    system = request.messages[0].content[0].text
    assert "agent-browser-cli" in system
