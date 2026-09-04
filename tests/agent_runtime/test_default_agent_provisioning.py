"""装完就该能回话：注册表为空时必须有一个可用的默认 Agent。

现场（2026-09-04，用户的 Telegram 截图）
--------------------------------------
用户在 Telegram 发一句话，收到的是：

    Workflow execution failed, please try again later:
    No Agent is configured for this channel identity

而 `data/agents/registry.json` **不存在**——一个 Agent 都没注册。

这不是 Telegram 特有的。四个 IM 适配器加 HTTP 入口全部用
`require_agent=True` 派发（`im_onebot_adapter/adapter.py:710`、
`im_qqbot_adapter/adapter.py:598`、`im_telegram_adapter/adapter.py:348`、
`im_wecom_adapter/adapter.py:551`、`im_http_legacy_adapter/adapter.py:179`），
而那个参数的含义就是「解析不到 Agent 就抛错」。于是一个新部署：
配好模型、配好渠道、**不去 Agent 页手建一个**，任何一句话都得到这个错。

为什么这是缺陷而不是「还没配」
----------------------------
需求 10 的关系模型是「渠道身份 → Agent → 上游模型/备用链 → 插件」，而
`ensure_builtins()` 已经预置了 12 条资源（提示词、记忆、MCP、Hook、Skill）。
也就是说「首屏就有货」只做了一半：**资源在，用它们的那个东西不在**。
一条链的第一环没有兜底，后面全部预置都到不了用户面前。

这组用例锁住的边界
----------------
1. 注册表为空且有可用模型时，冷启动后建出一个默认 Agent，且四个渠道都解析得到。
2. **已有 Agent 时一个字都不动**——这是兜底而不是「每次启动同步一份」，
   后者会覆盖用户的配置。
3. 没有任何可用模型时**不建**：建一个指向空模型链的 Agent，
   会把「没配模型」这个清晰的错误换成一次运行时解析失败。
4. 只取 `type == "llm"` 的模型：拿 `image_generation` 当主模型，
   第一次对话必然失败，而报错指向模型不支持对话——与「我没配 Agent」无关。
5. 建出来的 Agent 绑上已装好的内置提示词与记忆（那是需求 10 点名的那份），
   但**不绑 MCP 与 Hook**：那两类会起进程、能操作服务器，
   默认就绑等于替用户决定「装完立刻可以动服务器」。
6. 兜底失败不能阻断启动：`ensure_builtins()` 已经是这个约定（出网失败只降级），
   建默认 Agent 更不该让服务起不来。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.agent_runtime import AgentRegistry, ChannelContext
from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig, ModelConfig
from kirara_ai.llm.model_types import LLMAbility, ModelType
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService


def _backend(name: str = "openai-main", *, enable: bool = True, models=None):
    return LLMBackendConfig(
        name=name,
        adapter="openai",
        config={"api_key": "x", "api_base": "https://example.test/v1"},
        enable=enable,
        models=models
        if models is not None
        else [ModelConfig(id="gpt-5.6", type=ModelType.LLM.value, ability=LLMAbility.TextChat.value)],
    )


def _context(channel: str) -> ChannelContext:
    return ChannelContext(
        channel_type=channel,
        adapter_instance=f"{channel}-main",
        account_scope="main",
        conversation_scope="c2c:someone",
        sender_scope="someone",
    )


def _provision(tmp_path: Path, config: GlobalConfig, *, with_resources: bool = True):
    """跑一次真实的兜底装配，返回重新载入磁盘后的注册表。

    返回**重新构造**的 `AgentRegistry` 而不是内存里那个：兜底必须落盘，
    否则重启之后又回到「没有 Agent」——那种缺陷第一次对话是好的，
    重启之后坏，最难定位。
    """
    from kirara_ai.entry import provision_default_agent

    lifecycle = ResourceLifecycleService(tmp_path)
    if with_resources:
        ResourceCatalogService(lifecycle).ensure_builtins()
    registry = AgentRegistry(tmp_path)
    provision_default_agent(registry, config, lifecycle)
    return AgentRegistry(tmp_path), lifecycle


class TestAFreshDeploymentCanReply:
    def test_a_default_agent_is_provisioned_when_none_exists(self, tmp_path: Path):
        config = GlobalConfig()
        config.llms.api_backends.append(_backend())

        registry, _ = _provision(tmp_path, config)

        assert registry.agents, "空注册表 + 有可用模型时必须建出一个默认 Agent"
        assert registry.default_agent_id is not None

    @pytest.mark.parametrize(
        "channel", ["webui", "onebot", "qqbot", "telegram", "wecom", "http"]
    )
    def test_every_channel_resolves_to_it(self, tmp_path: Path, channel: str):
        """六个入口都必须解析得到——它们全部用 `require_agent=True` 派发。"""
        config = GlobalConfig()
        config.llms.api_backends.append(_backend())

        registry, _ = _provision(tmp_path, config)

        assert registry.resolve(_context(channel)).agent_id == registry.default_agent_id

    def test_the_model_chain_comes_from_the_configured_backend(self, tmp_path: Path):
        config = GlobalConfig()
        config.llms.api_backends.append(
            _backend(models=[ModelConfig(id="my-chat-model", type=ModelType.LLM.value, ability=LLMAbility.TextChat.value)])
        )

        registry, _ = _provision(tmp_path, config)

        agent = registry.get(registry.default_agent_id)
        assert "my-chat-model" in agent.model_priority

    def test_it_survives_a_restart(self, tmp_path: Path):
        """兜底必须落盘。只在内存里生效的话，重启之后又不能回话了。"""
        config = GlobalConfig()
        config.llms.api_backends.append(_backend())
        _provision(tmp_path, config)

        reloaded = AgentRegistry(tmp_path)

        assert reloaded.default_agent_id is not None
        assert reloaded.resolve(_context("telegram")) is not None


class TestItNeverOverwritesUserConfiguration:
    def test_an_existing_agent_is_left_alone(self, tmp_path: Path):
        """这是兜底而不是「每次启动同步一份」。"""
        from kirara_ai.agent_runtime import AgentDefinition
        from kirara_ai.entry import provision_default_agent

        config = GlobalConfig()
        config.llms.api_backends.append(_backend())
        registry = AgentRegistry(tmp_path)
        mine = AgentDefinition(agent_id="mine", model_priority=("my-own-model",))
        registry.register(mine)
        registry.configure(mine, is_default=True)

        provision_default_agent(registry, config, ResourceLifecycleService(tmp_path))

        assert set(registry.agents) == {"mine"}
        assert registry.default_agent_id == "mine"

    def test_an_existing_non_default_agent_also_blocks_provisioning(self, tmp_path: Path):
        """有 Agent 但没设默认，同样不介入。

        那可能是用户刻意的（按渠道分别绑定，不要全局兜底）。替他补一个默认 Agent
        会让本该「没有匹配就拒绝」的渠道悄悄开始回话。
        """
        from kirara_ai.agent_runtime import AgentDefinition
        from kirara_ai.entry import provision_default_agent

        config = GlobalConfig()
        config.llms.api_backends.append(_backend())
        registry = AgentRegistry(tmp_path)
        registry.register(AgentDefinition(agent_id="channel-only", model_priority=("m",)))

        provision_default_agent(registry, config, ResourceLifecycleService(tmp_path))

        assert set(registry.agents) == {"channel-only"}
        assert registry.default_agent_id is None


class TestItRefusesToGuessWhenThereIsNothingToRunOn:
    def test_no_backend_means_no_agent(self, tmp_path: Path):
        """建一个指向空模型链的 Agent，会把「没配模型」换成一次运行时解析失败。"""
        registry, _ = _provision(tmp_path, GlobalConfig())

        assert not registry.agents

    def test_a_disabled_backend_does_not_count(self, tmp_path: Path):
        config = GlobalConfig()
        config.llms.api_backends.append(_backend(enable=False))

        registry, _ = _provision(tmp_path, config)

        assert not registry.agents

    def test_a_backend_without_models_does_not_count(self, tmp_path: Path):
        config = GlobalConfig()
        config.llms.api_backends.append(_backend(models=[]))

        registry, _ = _provision(tmp_path, config)

        assert not registry.agents

    def test_non_chat_models_are_skipped(self, tmp_path: Path):
        """`image_generation` 当主模型会让第一次对话必然失败。

        而那时的报错指向「模型不支持对话」，与「我没配 Agent」毫无关系——
        用户会去查模型配置，而问题在一个他没做过的自动决定里。
        """
        config = GlobalConfig()
        config.llms.api_backends.append(
            _backend(
                models=[
                    ModelConfig(id="gpt-image-1", type=ModelType.ImageGeneration.value, ability=LLMAbility.ImageGeneration.value),
                    ModelConfig(id="text-embed", type=ModelType.Embedding.value, ability=LLMAbility.TextInput.value),
                ]
            )
        )

        registry, _ = _provision(tmp_path, config)

        assert not registry.agents, "只有非对话模型时不该建 Agent"

    def test_a_chat_model_is_picked_over_an_image_model(self, tmp_path: Path):
        config = GlobalConfig()
        config.llms.api_backends.append(
            _backend(
                models=[
                    ModelConfig(id="gpt-image-1", type=ModelType.ImageGeneration.value, ability=LLMAbility.ImageGeneration.value),
                    ModelConfig(id="gpt-5.6", type=ModelType.LLM.value, ability=LLMAbility.TextChat.value),
                ]
            )
        )

        registry, _ = _provision(tmp_path, config)

        agent = registry.get(registry.default_agent_id)
        assert agent.model_priority[0] == "gpt-5.6"
        assert "gpt-image-1" not in agent.model_priority


class TestWhatTheDefaultAgentIsAllowedToDo:
    def test_it_binds_the_builtin_prompt_and_memory(self, tmp_path: Path):
        """需求 10 点名的那份提示词要真的生效，否则预置了也白预置。"""
        config = GlobalConfig()
        config.llms.api_backends.append(_backend())

        registry, lifecycle = _provision(tmp_path, config)

        agent = registry.get(registry.default_agent_id)
        assert [item.resource_id for item in agent.prompt_bindings] == [
            "prompt.office-research"
        ]
        assert [item.resource_id for item in agent.memory_bindings] == [
            "memory.research-context"
        ]

    def test_it_does_not_bind_mcp_or_hooks(self, tmp_path: Path):
        """那两类会起进程、能操作服务器。

        默认就绑等于替用户决定「装完立刻可以动服务器」——而需求 10 对这件事
        的要求是显式确认。
        """
        config = GlobalConfig()
        config.llms.api_backends.append(_backend())

        registry, _ = _provision(tmp_path, config)

        agent = registry.get(registry.default_agent_id)
        assert agent.mcp_bindings == ()
        assert agent.hook_bindings == ()

    def test_the_bound_resources_are_enabled(self, tmp_path: Path):
        """绑一条停用的资源等于没绑：运行时会跳过它。"""
        config = GlobalConfig()
        config.llms.api_backends.append(_backend())

        _, lifecycle = _provision(tmp_path, config)

        for resource_id in ("prompt.office-research", "memory.research-context"):
            assert lifecycle.get_resource(resource_id)["enabled"] is True

    def test_missing_builtin_resources_do_not_block_provisioning(self, tmp_path: Path):
        """内置资源没装上时仍然要建出 Agent。

        `ensure_builtins()` 在离线部署里会降级跳过条目。那时「能回话」比
        「带着提示词回话」重要——没有 Agent 是完全不能用，
        没有提示词只是少一层行为约束。
        """
        config = GlobalConfig()
        config.llms.api_backends.append(_backend())

        registry, _ = _provision(tmp_path, config, with_resources=False)

        assert registry.default_agent_id is not None
        agent = registry.get(registry.default_agent_id)
        assert agent.prompt_bindings == ()


class TestProvisioningNeverBreaksStartup:
    def test_a_broken_registry_path_does_not_raise(self, tmp_path: Path):
        """兜底失败只降级，不阻断启动。

        `ensure_builtins()` 已经是这个约定（出网失败只跳过条目）。
        让「建一个默认 Agent」把服务拖死，比没有这个功能更糟。
        """
        from kirara_ai.entry import provision_default_agent

        config = GlobalConfig()
        config.llms.api_backends.append(_backend())

        class _Exploding(AgentRegistry):
            def register(self, agent):  # type: ignore[override]
                raise RuntimeError("disk is full")

        registry = _Exploding(tmp_path)

        # 不抛异常即为通过。
        provision_default_agent(registry, config, ResourceLifecycleService(tmp_path))

        assert not registry.agents

    def test_a_missing_resource_service_is_tolerated(self, tmp_path: Path):
        """嵌入式用法可能没有资源服务。缺它只少绑几条资源。"""
        from kirara_ai.entry import provision_default_agent

        config = GlobalConfig()
        config.llms.api_backends.append(_backend())
        registry = AgentRegistry(tmp_path)

        provision_default_agent(registry, config, None)

        assert registry.default_agent_id is not None


class TestTheProvisioningLogIsUsable:
    """这条日志是用户判断「机器人为什么能回话了」的唯一线索。

    实测抓到过一个真实缺陷：三处日志写成 `%s` 占位符，而这个项目用 loguru
    （占位符是 `{}`）。运行时不报错，只把 agent_id 与模型链原样打成两个
    字面量 `%s`。上面 22 条测试全绿——没有一条断言日志内容。
    """

    def test_the_created_agent_and_models_appear_in_the_log(self, tmp_path: Path):
        from loguru import logger

        from kirara_ai.entry import provision_default_agent

        config = GlobalConfig()
        config.llms.api_backends.append(
            _backend(
                models=[
                    ModelConfig(
                        id="gpt-5.6",
                        type=ModelType.LLM.value,
                        ability=LLMAbility.TextChat.value,
                    ),
                    ModelConfig(
                        id="claude-opus-5",
                        type=ModelType.LLM.value,
                        ability=LLMAbility.TextChat.value,
                    ),
                ]
            )
        )
        lifecycle = ResourceLifecycleService(tmp_path)
        ResourceCatalogService(lifecycle).ensure_builtins()

        lines: list[str] = []
        sink_id = logger.add(lambda message: lines.append(str(message)), level="INFO")
        try:
            provision_default_agent(AgentRegistry(tmp_path), config, lifecycle)
        finally:
            logger.remove(sink_id)

        joined = "\n".join(lines)
        assert "default-agent" in joined, "日志必须说出建了哪个 Agent"
        assert "gpt-5.6" in joined, "日志必须说出模型链，否则排查时看不出用了什么"
        # `%s` 原样出现就是占位符没被插值——loguru 用 `{}`。
        assert "%s" not in joined

    def test_the_skipped_reason_is_logged_when_there_is_no_model(self, tmp_path: Path):
        """不建的时候也要说清为什么，否则用户不知道该去配什么。"""
        from loguru import logger

        from kirara_ai.entry import provision_default_agent

        lines: list[str] = []
        sink_id = logger.add(lambda message: lines.append(str(message)), level="INFO")
        try:
            provision_default_agent(AgentRegistry(tmp_path), GlobalConfig(), None)
        finally:
            logger.remove(sink_id)

        joined = "\n".join(lines)
        assert "模型" in joined, "必须指向「去配一个模型」这个可执行的下一步"
        assert "%s" not in joined
