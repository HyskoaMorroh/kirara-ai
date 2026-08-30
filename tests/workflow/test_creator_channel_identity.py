"""需求 10(e)：创建者必须能**从 IM 渠道**触发受保护的插件能力。

需求原文要求：只有创建者能通过 Skills / Hooks / MCP / Prompts / Agents 修改
服务器内容或执行文件操作；其他使用者收到这类指令一律忽视，但仍然得到正常的
AI 回复。后半句一直是对的（`executor` 把工具列表清空而不是报错）。
前半句在 IM 渠道上**根本无法成立**：

`principal_can_control_agent` 是唯一门禁，而 principal 只由 HTTP Bearer 中间件
注入（`web/auth/middleware.py`）。OneBot / QQ / Telegram / WeCom 的入站链路
全程没有 principal，于是这些渠道上：

- MCP 工具列表恒为空；
- command 型 Hook 恒被拒（含内置的 `hook:ai-debug` 八个事件）；
- 需确认的宿主操作永远走不到确认那一步。

这不是「权限设计如此」——设计是「非创建者不行」，实现成了「所有人都不行」，
包括创建者本人。缺的是一座桥：把「这个 QQ 号就是创建者」这件事声明出来。

桥必须是**显式选择加入**的：默认空表，行为与此前逐字节一致。
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from kirara_ai.agent_runtime import ChannelContext
from kirara_ai.config.global_config import CreatorChannelIdentity, GlobalConfig
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.web.auth.principal import get_runtime_principal
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher
from kirara_ai.workflow.core.dispatch.registry import DispatchRuleRegistry
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry


class _Adapter:
    channel_type = "onebot"
    adapter_instance = "onebot-main"
    account_scope = "10001"


class _AuthService:
    """只提供创建者身份，不参与 HTTP 流程。"""

    creator_subject = "creator-subject-value"


def _message(user_id: str = "88888888", text: str = "装一下 context7") -> IMMessage:
    return IMMessage(
        ChatSender.from_c2c_chat(user_id, "Operator"),
        [TextMessage(text)],
    )


def _group_message(user_id: str = "88888888") -> IMMessage:
    return IMMessage(
        ChatSender.from_group_chat(user_id, "group-1", "Operator"),
        [TextMessage("装一下 context7")],
    )


def _dispatcher(identities: list[CreatorChannelIdentity], *, with_auth: bool = True):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    config.agent_runtime.creator_channel_identities = identities
    container.register(GlobalConfig, config)
    container.register(WorkflowRegistry, WorkflowRegistry(container))
    container.register(DispatchRuleRegistry, DispatchRuleRegistry(container))
    if with_auth:
        from kirara_ai.web.auth.services import AuthService

        container.register(AuthService, _AuthService())
    return WorkflowDispatcher(container)


def _resolve(dispatcher: WorkflowDispatcher, message: IMMessage) -> Optional[Any]:
    """Return the principal the dispatcher would establish for this message."""
    context = ChannelContext.from_message(_Adapter(), message)
    return dispatcher._creator_principal(context)


def test_no_declared_identity_means_no_principal():
    """默认空表：行为与此前完全一致，IM 侧拿不到任何身份。"""
    dispatcher = _dispatcher([])

    assert _resolve(dispatcher, _message()) is None


def test_a_declared_sender_on_the_right_channel_becomes_the_creator():
    dispatcher = _dispatcher(
        [CreatorChannelIdentity(channel_type="onebot", sender_scope="88888888")]
    )

    principal = _resolve(dispatcher, _message())

    assert principal is not None
    assert principal.is_creator is True
    # 主体必须是 `AuthService` 的创建者身份本身，否则
    # `principal_can_control_agent` 与 Agent 的 `owner_subject` 比不上。
    assert principal.subject == "creator-subject-value"


def test_a_different_sender_gets_nothing():
    dispatcher = _dispatcher(
        [CreatorChannelIdentity(channel_type="onebot", sender_scope="88888888")]
    )

    assert _resolve(dispatcher, _message(user_id="99999999")) is None


def test_the_same_sender_id_on_another_channel_gets_nothing():
    """QQ 号与 Telegram 用户 ID 可能撞号，渠道必须一起比。"""
    dispatcher = _dispatcher(
        [CreatorChannelIdentity(channel_type="telegram", sender_scope="88888888")]
    )

    assert _resolve(dispatcher, _message()) is None


def test_account_scope_can_narrow_the_declaration():
    """同一个人在两个机器人账号下出现时，可以只授权其中一个。"""
    dispatcher = _dispatcher(
        [
            CreatorChannelIdentity(
                channel_type="onebot",
                account_scope="20002",
                sender_scope="88888888",
            )
        ]
    )

    assert _resolve(dispatcher, _message()) is None

    dispatcher = _dispatcher(
        [
            CreatorChannelIdentity(
                channel_type="onebot",
                account_scope="10001",
                sender_scope="88888888",
            )
        ]
    )
    assert _resolve(dispatcher, _message()) is not None


def test_group_messages_are_opt_in_and_off_by_default():
    """群聊里默认不生效。

    群里任何人都能看到机器人，也能看到创建者发的指令并照抄。虽然
    `sender_scope` 仍然是发言人本人（照抄的人拿不到身份），但把宿主操作暴露在
    一个多人可见的会话里是另一回事：一条误发的消息会被所有人看到并模仿尝试。
    因此默认只在私聊生效，要群聊必须显式声明。
    """
    identity = CreatorChannelIdentity(channel_type="onebot", sender_scope="88888888")
    dispatcher = _dispatcher([identity])

    assert _resolve(dispatcher, _group_message()) is None

    dispatcher = _dispatcher(
        [
            CreatorChannelIdentity(
                channel_type="onebot",
                sender_scope="88888888",
                allow_group_chat=True,
            )
        ]
    )
    assert _resolve(dispatcher, _group_message()) is not None


def test_without_an_auth_service_no_principal_is_invented():
    """拿不到创建者身份时不能编一个出来。"""
    dispatcher = _dispatcher(
        [CreatorChannelIdentity(channel_type="onebot", sender_scope="88888888")],
        with_auth=False,
    )

    assert _resolve(dispatcher, _message()) is None


def test_an_empty_sender_scope_is_rejected_at_config_time():
    """空的 `sender_scope` 会匹配到「取不到用户 ID」的兜底值，等于对所有人放开。"""
    with pytest.raises(ValueError):
        CreatorChannelIdentity(channel_type="onebot", sender_scope="   ")


def test_wildcards_are_not_accepted_as_a_sender_scope():
    """`*` 只是一个普通字符串，不该被误当成通配。

    如果有人照着别处的配置写法填了 `*`，它必须匹配不到任何真实用户，
    而不是意外地匹配所有人。
    """
    dispatcher = _dispatcher(
        [CreatorChannelIdentity(channel_type="onebot", sender_scope="*")]
    )

    assert _resolve(dispatcher, _message()) is None


@pytest.mark.asyncio
async def test_dispatch_runs_inside_the_principal_context():
    """身份必须在整条派发链路上可见，而不只是算出来放着。"""
    dispatcher = _dispatcher(
        [CreatorChannelIdentity(channel_type="onebot", sender_scope="88888888")]
    )
    seen: list[Any] = []

    def record(_message, _stage, **_details):
        seen.append(get_runtime_principal())

    dispatcher._record_stage = record  # type: ignore[method-assign]

    await dispatcher.dispatch(_Adapter(), _message())

    assert seen, "派发过程中至少记录了一个阶段"
    assert seen[0] is not None
    assert seen[0].is_creator is True
    # 离开派发后必须恢复，不能把身份泄漏到后续任务里。
    assert get_runtime_principal() is None


@pytest.mark.asyncio
async def test_an_existing_http_principal_is_never_replaced():
    """WebUI 已经有身份时不得被这座桥覆盖或清空。

    这是最容易写错的一处：无条件 `runtime_principal_context(principal)` 在
    未声明任何渠道身份时会用 `None` 把 HTTP 中间件设好的身份**清掉**。
    那不是「IM 侧多一条路」，而是「HTTP 侧少一条路」——WebUI 的 MCP 工具、
    command Hook 会整体失效，而且症状与本次改动毫无表面关联。
    """
    from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context

    http_principal = RuntimePrincipal(
        subject="http-creator", is_creator=True, scopes=frozenset({"*"})
    )
    # 故意不声明任何渠道身份：桥不该在这种情况下做任何事。
    dispatcher = _dispatcher([])
    seen: list[Any] = []

    def record(_message, _stage, **_details):
        seen.append(get_runtime_principal())

    dispatcher._record_stage = record  # type: ignore[method-assign]

    with runtime_principal_context(http_principal):
        await dispatcher.dispatch(_Adapter(), _message())

    assert seen and seen[0] is http_principal


@pytest.mark.asyncio
async def test_a_declared_channel_does_not_override_an_http_principal():
    """两者同时存在时以已有身份为准，不做任何替换。"""
    from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context

    http_principal = RuntimePrincipal(subject="http-creator", is_creator=False)
    dispatcher = _dispatcher(
        [CreatorChannelIdentity(channel_type="onebot", sender_scope="88888888")]
    )
    seen: list[Any] = []

    def record(_message, _stage, **_details):
        seen.append(get_runtime_principal())

    dispatcher._record_stage = record  # type: ignore[method-assign]

    with runtime_principal_context(http_principal):
        await dispatcher.dispatch(_Adapter(), _message())

    # 提权路径只能有一条。已有身份就用它，否则「同一个请求算不算创建者」
    # 会取决于两处配置的组合，而那是无法审计的。
    assert seen and seen[0] is http_principal
    assert seen[0].is_creator is False
