"""Agent 级 `reply_stream_mode` 必须能通过 REST 读到、也能改（需求 4）。

落盘那一半在 `tests/agent_runtime/test_reply_stream_mode_persistence.py`。这里钉
接口那一半：`_agent_payload` 与 `_agent_from_payload` 必须成对含有这个字段。

只读不写等于「界面能看见但改不了」，只写不读等于「改了看不见」——两者都是半个
功能，而半个功能比没有功能更容易让人误判：运维在界面上看到 `incremental`，
以为已经生效。

`exclude_unset` 语义同样要保住：请求体里不带这个键时保留已存的值，
而不是把它重置成默认档。否则任何一次只改显示名的保存都会顺手把流式档位清掉。
"""

from __future__ import annotations

import pytest

from kirara_ai.agent_runtime.core import AgentDefinition, AgentRegistry
from kirara_ai.web.api.agent.routes import _agent_from_payload, _agent_payload


def _agent(**kwargs) -> AgentDefinition:
    kwargs.setdefault("model_priority", ("openai:gpt-4o",))
    return AgentDefinition(agent_id="office", display_name="办公助手", **kwargs)


def _registered(agent: AgentDefinition) -> AgentRegistry:
    """一个注册了该 Agent 的内存注册表。

    `_agent_payload` 会调 `relation_summary(agent_id)`，那要求这个 Agent 真的在
    注册表里——传一个空注册表会得到 `LookupError`，而那与本文件要验证的字段无关。
    """
    registry = AgentRegistry(None)
    registry.register(agent)
    return registry


class TestTheFieldIsReadable:
    def test_the_payload_carries_the_mode(self):
        agent = _agent(reply_stream_mode="incremental")
        payload = _agent_payload(agent, _registered(agent))

        assert payload["reply_stream_mode"] == "incremental"

    def test_the_default_is_reported_as_inherit(self):
        agent = _agent()
        payload = _agent_payload(agent, _registered(agent))

        assert payload["reply_stream_mode"] == "inherit"


class TestTheFieldIsWritable:
    def test_a_new_agent_can_declare_the_mode(self):
        agent = _agent_from_payload(
            {
                "agent_id": "office",
                "display_name": "办公助手",
                "model_priority": ["openai:gpt-4o"],
                "reply_stream_mode": "aggregate",
            }
        )

        assert agent.reply_stream_mode == "aggregate"

    def test_an_existing_agent_can_change_the_mode(self):
        agent = _agent_from_payload(
            {"reply_stream_mode": "incremental"}, existing=_agent(reply_stream_mode="off")
        )

        assert agent.reply_stream_mode == "incremental"

    def test_omitting_the_key_keeps_the_stored_value(self):
        """只改显示名的保存不能顺手把流式档位清掉。"""
        agent = _agent_from_payload(
            {"display_name": "新名字"}, existing=_agent(reply_stream_mode="incremental")
        )

        assert agent.reply_stream_mode == "incremental"

    def test_a_new_agent_without_the_key_inherits(self):
        agent = _agent_from_payload(
            {
                "agent_id": "office",
                "display_name": "办公助手",
                "model_priority": ["openai:gpt-4o"],
            }
        )

        assert agent.reply_stream_mode == "inherit"

    def test_an_invalid_mode_is_refused(self):
        with pytest.raises(ValueError):
            _agent_from_payload(
                {"reply_stream_mode": "sometimes"}, existing=_agent()
            )

    @pytest.mark.parametrize("mode", ["off", "aggregate", "incremental", "inherit"])
    def test_every_declared_mode_round_trips(self, mode):
        agent = _agent_from_payload({"reply_stream_mode": mode}, existing=_agent())

        assert _agent_payload(agent, _registered(agent))["reply_stream_mode"] == mode
