"""`SessionEnd` Hook 必须在会话被清理时真的触发（需求 10）。

`HOOK_EVENTS` 声明了它，`audit_hook_command.py` 的事件白名单收了它，
`hooks.py` 也为它留了解析位置。但 executor 从不派发——用户挂一个
`SessionEnd` 钩子做收尾清理，它能通过声明校验、能启用、能在界面上显示为
"已启用"，运行时一次都不跑。

派发点是会话被清理的两个入口：
  DELETE /agents/sessions/<id>          删掉整个会话
  DELETE /agents/sessions/<id>/history  只清历史、保留绑定

难点在于 Hook 需要 `ChannelContext`（渠道类型、账号、会话、发送者四个标识），
而会话文件按 `[session_key, agent_id]` 的 SHA-256 摘要命名，**原始渠道身份
没有存进文件**。两条错误的走法：

- 用占位 context 派发。Hook 会拿到一个虚构的渠道身份写进审计——比缺一个事件
  更糟，因为审计记录从此不可信。
- 把 `SessionEnd` 从契约里删掉。那是把"没实现"改写成"不支持"，而三处代码都
  为它留了位置，设计意图明确。

正确的走法是让会话文件记住自己的渠道身份：写历史时一并存下 context 的四个
标识，清理时读出来还原。这是新增字段，旧文件读不到就跳过派发（缺字段说明
它是本次改动之前写的，此时不派发比派发一个编出来的身份更诚实）。

这里锁住：字段被持久化、能读回、清理时触发、旧文件不派发也不报错。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.agent_runtime.core import ChannelContext
from kirara_ai.agent_runtime.session_store import SessionStore
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatMessage


def _context() -> ChannelContext:
    return ChannelContext(
        channel_type="onebot",
        adapter_instance="bot-a",
        account_scope="10001",
        conversation_scope="private:20002",
        sender_scope="20002",
    )


def _messages() -> list[LLMChatMessage]:
    return [
        LLMChatMessage(role="user", content=[LLMChatTextContent(text="你好")]),
        LLMChatMessage(role="assistant", content=[LLMChatTextContent(text="在")]),
    ]


def test_channel_identity_is_persisted_with_the_history(tmp_path: Path):
    """写历史时存下渠道身份，`SessionEnd` 才有真实的 context 可用。"""
    store = SessionStore(tmp_path)
    context = _context()

    store.save_history(
        context.session_key, _messages(), agent_id="agent-a", context=context
    )

    listed = store.list_sessions()
    assert len(listed) == 1
    identity = listed[0]["channel_identity"]
    assert identity == {
        "channel_type": "onebot",
        "adapter_instance": "bot-a",
        "account_scope": "10001",
        "conversation_scope": "private:20002",
        "sender_scope": "20002",
    }


def test_the_identity_can_be_read_back_for_one_session(tmp_path: Path):
    """清理路由手里只有 session_id，必须能由它反查渠道身份。"""
    store = SessionStore(tmp_path)
    context = _context()
    store.save_history(
        context.session_key, _messages(), agent_id="agent-a", context=context
    )
    session_id = store.list_sessions()[0]["session_id"]

    metadata = store.read_session_metadata(session_id)

    assert metadata is not None
    assert metadata["agent_id"] == "agent-a"
    restored = metadata["channel_identity"]
    assert restored["channel_type"] == "onebot"
    assert restored["sender_scope"] == "20002"


def test_a_session_written_without_context_reports_no_identity(tmp_path: Path):
    """旧文件没有这个字段。读回时给 None，让调用方跳过派发而不是编一个身份。"""
    store = SessionStore(tmp_path)
    context = _context()
    store.save_history(context.session_key, _messages(), agent_id="agent-a")
    session_id = store.list_sessions()[0]["session_id"]

    metadata = store.read_session_metadata(session_id)

    assert metadata is not None
    assert metadata["channel_identity"] is None


def test_reading_an_unknown_session_returns_none(tmp_path: Path):
    """未知 session_id 返回 None，而不是抛错——清理路由要给 404 而不是 500。"""
    store = SessionStore(tmp_path)

    assert store.read_session_metadata("0" * 64) is None


def test_a_malformed_session_id_is_refused(tmp_path: Path):
    """路径穿越形态的 id 一律拒绝，与 `_session_path` 的既有约束一致。"""
    store = SessionStore(tmp_path)

    for bad in ("../pending", "a/b", "", "zz" * 32):
        assert store.read_session_metadata(bad) is None


def test_clearing_history_keeps_the_channel_identity(tmp_path: Path):
    """只清历史时会话仍然存在，渠道身份不能一起丢。

    丢了的后果是：清一次历史之后，这个会话再也派发不出 `SessionEnd`。
    """
    store = SessionStore(tmp_path)
    context = _context()
    store.save_history(
        context.session_key, _messages(), agent_id="agent-a", context=context
    )
    session_id = store.list_sessions()[0]["session_id"]

    assert store.clear_history(session_id) is True

    metadata = store.read_session_metadata(session_id)
    assert metadata is not None
    assert metadata["channel_identity"] is not None
    assert metadata["channel_identity"]["channel_type"] == "onebot"


def test_the_identity_survives_a_history_rewrite(tmp_path: Path):
    """同一会话反复写历史，身份保持一致而不是被后一次覆盖成空。"""
    store = SessionStore(tmp_path)
    context = _context()
    store.save_history(
        context.session_key, _messages(), agent_id="agent-a", context=context
    )
    store.save_history(
        context.session_key, _messages(), agent_id="agent-a", context=context
    )

    listed = store.list_sessions()
    assert len(listed) == 1
    assert listed[0]["channel_identity"]["sender_scope"] == "20002"


def test_a_restored_context_equals_the_original(tmp_path: Path):
    """还原出的 context 必须与原始的逐字段相等，否则 Hook 收到的是另一个身份。"""
    store = SessionStore(tmp_path)
    context = _context()
    store.save_history(
        context.session_key, _messages(), agent_id="agent-a", context=context
    )
    session_id = store.list_sessions()[0]["session_id"]

    identity = store.read_session_metadata(session_id)["channel_identity"]
    restored = ChannelContext(**identity)

    assert restored == context
    assert restored.session_key == context.session_key
