"""会话被清理时必须派发 `SessionEnd` Hook（需求 10）。

前一份测试（`test_session_channel_identity.py`）锁住了渠道身份能落盘、能读回。
这一份锁住它被真正用起来：两个清理入口都要触发 `SessionEnd`。

两个入口的语义不同，都要覆盖：
  DELETE /agents/sessions/<id>          会话消失
  DELETE /agents/sessions/<id>/history  会话保留、历史归零

四条边界，每一条都对应一种"看起来正常其实坏了"：

- **旧会话（无渠道身份）不派发，但清理照常成功。** 派发一个编出来的身份会污染
  审计；而因为拿不到身份就拒绝清理，等于让用户删不掉旧会话。
- **Hook 失败不能挡住清理。** Hook 是策略副作用，不是清理的前置条件。一个写坏的
  钩子不该让"删会话"这个操作从此不可用。
- **未知会话不派发。** 404 路径上没有任何会话被结束。
- **派发发生在清理之后。** Hook 里若去读这个会话，应当看到它已经被清理——
  否则 Hook 观察到的状态与它被告知的事件相矛盾。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kirara_ai.agent_runtime.core import ChannelContext
from kirara_ai.agent_runtime.session_store import SessionStore
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatMessage
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


class _RecordingExecutor:
    """只记录 `SessionEnd` 派发，不跑真正的 Hook。"""

    def __init__(self, *, fail: bool = False):
        self.calls: list[dict[str, Any]] = []
        self.fail = fail
        self.session_snapshot_at_call: list[Any] = []
        self.store: SessionStore | None = None

    async def dispatch_session_end(
        self, *, session_id: str, agent_id: str | None, context: ChannelContext
    ) -> None:
        # 记录派发瞬间会话是否还在，用来验证「清理之后才派发」。
        if self.store is not None:
            self.session_snapshot_at_call.append(
                self.store.read_session_metadata(session_id)
            )
        self.calls.append(
            {"session_id": session_id, "agent_id": agent_id, "context": context}
        )
        if self.fail:
            raise RuntimeError("hook exploded")


def _context() -> ChannelContext:
    return ChannelContext(
        channel_type="telegram",
        adapter_instance="bot-x",
        account_scope="acct",
        conversation_scope="private:777",
        sender_scope="777",
    )


def _make_api(tmp_path: Path, *, executor: _RecordingExecutor | None = None):
    from kirara_ai.agent_runtime import AgentRuntimeExecutor

    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(
        AuthService, MockAuthService(scopes=["*"], subject="creator")
    )
    store = SessionStore(tmp_path / "sessions")
    container.register(SessionStore, store)
    if executor is not None:
        executor.store = store
        container.register(AgentRuntimeExecutor, executor)
    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), store, executor


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


def _seed(store: SessionStore, *, with_context: bool = True) -> str:
    context = _context()
    store.save_history(
        context.session_key,
        [LLMChatMessage(role="user", content=[LLMChatTextContent(text="hi")])],
        agent_id="agent-a",
        context=context if with_context else None,
    )
    return store.list_sessions()[0]["session_id"]


@pytest.mark.asyncio
async def test_deleting_a_session_dispatches_session_end(tmp_path: Path):
    executor = _RecordingExecutor()
    client, store, _ = _make_api(tmp_path, executor=executor)
    session_id = _seed(store)

    response = await client.delete(
        f"/api/agents/sessions/{session_id}", headers=_headers()
    )

    assert response.status_code == 200
    assert len(executor.calls) == 1
    call = executor.calls[0]
    assert call["session_id"] == session_id
    assert call["agent_id"] == "agent-a"
    assert call["context"] == _context()


@pytest.mark.asyncio
async def test_clearing_history_dispatches_session_end(tmp_path: Path):
    """清历史也是会话生命周期的终点——那一轮的上下文不再存在。"""
    executor = _RecordingExecutor()
    client, store, _ = _make_api(tmp_path, executor=executor)
    session_id = _seed(store)

    response = await client.delete(
        f"/api/agents/sessions/{session_id}/history", headers=_headers()
    )

    assert response.status_code == 200
    assert len(executor.calls) == 1
    assert executor.calls[0]["context"] == _context()


@pytest.mark.asyncio
async def test_the_hook_sees_the_session_already_cleared(tmp_path: Path):
    """派发在清理之后：Hook 若去读这个会话，应当看到它已经不在了。"""
    executor = _RecordingExecutor()
    client, store, _ = _make_api(tmp_path, executor=executor)
    session_id = _seed(store)

    await client.delete(f"/api/agents/sessions/{session_id}", headers=_headers())

    assert executor.session_snapshot_at_call == [None]


@pytest.mark.asyncio
async def test_an_old_session_without_identity_is_still_deletable(tmp_path: Path):
    """旧会话没有渠道身份：不派发，但清理照常成功。

    因为拿不到身份就拒绝清理，等于让用户永远删不掉升级前留下的会话。
    """
    executor = _RecordingExecutor()
    client, store, _ = _make_api(tmp_path, executor=executor)
    session_id = _seed(store, with_context=False)

    response = await client.delete(
        f"/api/agents/sessions/{session_id}", headers=_headers()
    )

    assert response.status_code == 200
    assert executor.calls == []
    assert store.read_session_metadata(session_id) is None


@pytest.mark.asyncio
async def test_a_failing_hook_does_not_block_the_cleanup(tmp_path: Path):
    """Hook 是策略副作用。一个写坏的钩子不该让「删会话」从此不可用。"""
    executor = _RecordingExecutor(fail=True)
    client, store, _ = _make_api(tmp_path, executor=executor)
    session_id = _seed(store)

    response = await client.delete(
        f"/api/agents/sessions/{session_id}", headers=_headers()
    )

    assert response.status_code == 200
    assert len(executor.calls) == 1
    assert store.read_session_metadata(session_id) is None


@pytest.mark.asyncio
async def test_an_unknown_session_dispatches_nothing(tmp_path: Path):
    """404 路径上没有任何会话被结束。"""
    executor = _RecordingExecutor()
    client, _, _ = _make_api(tmp_path, executor=executor)

    response = await client.delete(
        f"/api/agents/sessions/{'0' * 64}", headers=_headers()
    )

    assert response.status_code == 404
    assert executor.calls == []


@pytest.mark.asyncio
async def test_cleanup_works_without_the_agent_runtime(tmp_path: Path):
    """没接 Agent 运行时的部署（或裸容器单测）照样能清理会话。"""
    client, store, _ = _make_api(tmp_path, executor=None)
    session_id = _seed(store)

    response = await client.delete(
        f"/api/agents/sessions/{session_id}", headers=_headers()
    )

    assert response.status_code == 200
    assert store.read_session_metadata(session_id) is None
