"""需求 4：流式模式必须能按 Agent 与按渠道配，不能只有一个进程级开关。

`reply_stream_mode` 原本只存在于 `AgentRuntimeConfig`，在
`AgentRuntimeExecutor.__init__` 时读一次存成 `self.reply_stream_mode`。
后果是一个部署里**所有** Agent、所有渠道共用同一档：

* 一个接了慢上游、需要首字节超时保护的 Agent 要打开流式；
* 同一部署里另一个走本地小模型、逐次请求毫秒级返回的 Agent 打开它只是白付一次
  流式握手；
* WebUI 能逐步渲染，QQ 不能——渠道之间本就该不同。

于是运维只能二选一，而两种选择都对一部分入口是错的。

优先级：**Agent 显式声明 > 渠道默认 > 进程默认**。三层都缺省时行为与此前逐字节一致
（`off`），升级不会让任何部署突然改变取回方式。

`inherit` 是一个刻意的第四取值：它与「没设置」不同——它表示「我明确要求跟随上层」，
因此在 Agent 上写 `inherit` 与不写效果相同，但意图可读，也能把一个曾经被显式设成
`off` 的 Agent 改回跟随，而不必猜上层是什么。
"""

from __future__ import annotations

import pytest

from kirara_ai.agent_runtime.core import AgentDefinition
from kirara_ai.agent_runtime.executor import resolve_reply_stream_mode


def _agent(mode: str | None = None) -> AgentDefinition:
    kwargs = {"agent_id": "a", "model_priority": ("m",)}
    if mode is not None:
        kwargs["reply_stream_mode"] = mode
    return AgentDefinition(**kwargs)


def test_agent_declaration_wins_over_channel_and_process():
    resolved = resolve_reply_stream_mode(
        agent_mode=_agent("aggregate").reply_stream_mode,
        channel_modes={"onebot": "off"},
        channel_type="onebot",
        process_mode="off",
    )

    # 回归点：原实现只看进程级值，这里会是 "off"。
    assert resolved == "aggregate"


def test_channel_default_applies_when_the_agent_inherits():
    resolved = resolve_reply_stream_mode(
        agent_mode="inherit",
        channel_modes={"webui": "aggregate", "onebot": "off"},
        channel_type="webui",
        process_mode="off",
    )

    # WebUI 能逐步渲染、QQ 不能：渠道之间本就该不同，而此前只能二选一。
    assert resolved == "aggregate"


def test_process_default_applies_when_neither_agent_nor_channel_declares():
    resolved = resolve_reply_stream_mode(
        agent_mode="inherit",
        channel_modes={},
        channel_type="onebot",
        process_mode="aggregate",
    )

    assert resolved == "aggregate"


def test_everything_absent_falls_back_to_off():
    resolved = resolve_reply_stream_mode(
        agent_mode="inherit",
        channel_modes={},
        channel_type="onebot",
        process_mode="off",
    )

    # 三层都缺省时与升级前逐字节一致。不存在「升级之后取回方式突然变了」。
    assert resolved == "off"


def test_a_channel_entry_for_another_channel_does_not_leak():
    resolved = resolve_reply_stream_mode(
        agent_mode="inherit",
        channel_modes={"webui": "aggregate"},
        channel_type="onebot",
        process_mode="off",
    )

    # 给 WebUI 开流式不该顺带把 QQ 也开了：那正是「一个开关管所有入口」的问题。
    assert resolved == "off"


def test_unknown_channel_type_uses_the_process_default():
    resolved = resolve_reply_stream_mode(
        agent_mode="inherit",
        channel_modes={"webui": "aggregate"},
        channel_type=None,
        process_mode="aggregate",
    )

    assert resolved == "aggregate"


@pytest.mark.parametrize("bogus", ["", "  ", "streaming", "on", "true", None])
def test_unrecognized_values_are_treated_as_inherit_not_as_enabled(bogus):
    resolved = resolve_reply_stream_mode(
        agent_mode=bogus,
        channel_modes={},
        channel_type="onebot",
        process_mode="off",
    )

    # 一个写错的值必须**不启用**流式。反过来（把无法识别的值当成开启）会让一处拼写
    # 错误静默改变整条取回路径，而配置界面上看起来是有效的。
    assert resolved == "off"


def test_agent_rejects_an_invalid_declaration_at_definition_time():
    # 在定义期就拒绝，而不是等到某一轮对话才发现：一个不合法的取值如果被静默忽略，
    # 运维会以为流式已经开了，然后去排查一个不存在的上游问题。
    with pytest.raises(ValueError, match="reply_stream_mode"):
        _agent("streaming")


def test_agent_default_is_inherit_so_existing_registries_keep_working():
    # 早于本特性的 `registry.json` 没有这个键。缺省必须是 `inherit`，
    # 否则升级会把所有既有 Agent 的取回方式改掉。
    assert _agent().reply_stream_mode == "inherit"


def test_a_streaming_entry_point_opts_in_without_configuration():
    """按流式协议接住回复的入口（WebUI 的 SSE 路由）默认就该拿到增量。

    否则那条路由在**默认配置**下只会发 `start` 与 `done`：功能写完了，
    而所有没特意改过配置的部署上都不生效。这类「已实现但默认关闭到等于没有」
    的形态，比功能缺失更难发现——代码、测试、文档都在，只有用户看不到。
    """
    resolved = resolve_reply_stream_mode(
        agent_mode="inherit",
        channel_modes={},
        channel_type="webui",
        process_mode="off",
        request_mode="incremental",
    )

    assert resolved == "incremental"


def test_an_explicit_channel_setting_still_overrides_the_entry_point():
    """运维写下的取值优先于入口的默认主张。

    把 `webui` 显式配成 `off` 的部署（例如反向代理关掉了分块传输）必须真的拿到
    非流式，否则那句配置是装饰。
    """
    resolved = resolve_reply_stream_mode(
        agent_mode="inherit",
        channel_modes={"webui": "off"},
        channel_type="webui",
        process_mode="aggregate",
        request_mode="incremental",
    )

    assert resolved == "off"


def test_an_explicit_agent_setting_still_wins_over_the_entry_point():
    resolved = resolve_reply_stream_mode(
        agent_mode="off",
        channel_modes={},
        channel_type="webui",
        process_mode="aggregate",
        request_mode="incremental",
    )

    assert resolved == "off"


def test_the_entry_point_claim_outranks_only_the_process_default():
    """入口声明排在进程默认**之前**，但仅此而已。"""
    resolved = resolve_reply_stream_mode(
        agent_mode="inherit",
        channel_modes={},
        channel_type="webui",
        process_mode="aggregate",
        request_mode="incremental",
    )

    assert resolved == "incremental"


@pytest.mark.parametrize("bogus", ["", "  ", "streaming", "on", "true"])
def test_an_unrecognized_entry_point_claim_is_ignored(bogus):
    resolved = resolve_reply_stream_mode(
        agent_mode="inherit",
        channel_modes={},
        channel_type="webui",
        process_mode="off",
        request_mode=bogus,
    )

    assert resolved == "off"


def test_omitting_the_entry_point_claim_keeps_the_previous_behaviour():
    """不传时逐字节等于本参数不存在时——IM 适配器一条路径都不变。"""
    assert (
        resolve_reply_stream_mode(
            agent_mode="inherit",
            channel_modes={},
            channel_type="onebot",
            process_mode="aggregate",
        )
        == "aggregate"
    )
