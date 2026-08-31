"""`get_bot_profile` 的零测试覆盖补上（需求 7、12）。

这个方法被两个 Web 路由消费（`kirara_ai/web/api/im/routes.py:182` 与 `:364`），
是「机器人」页显示机器人自己头像与昵称的唯一来源。此前 `tests/` 里对
`get_bot_profile` 与 `get_login_info` 都是零命中——包括那条「未连接时返回占位」
的分支，而那条分支恰恰是 QQ 没登录时用户唯一看到的东西。

补覆盖的理由不是「凑数」：这个方法有一处不容易看出的正确性依赖——
它会把上游回报的 `user_id` **写回 `self.self_id`**。多账号部署里
`self.self_id` 参与动作路由（`_action_self_id`），所以「查一次机器人资料」
会有副作用。副作用本身是有意的（那是唯一能确认当前登录账号的时机），
但它必须只在**成功**时发生：失败路径写回一个占位值会让后续动作路由到
一个不存在的账号。
"""

from __future__ import annotations

import pytest

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig

LOGIN_INFO = {"user_id": 1726256417, "nickname": "研究助手"}


def make_adapter() -> OneBotAdapter:
    container = DependencyContainer()
    container.register(OneBotConfig, OneBotConfig())
    return Inject(container).create(OneBotAdapter)()


def _stub_action(adapter: OneBotAdapter, result, *, raises: bool = False):
    calls: list[dict] = []

    async def _call_action(action: str, **params):
        calls.append({"action": action, **params})
        if raises:
            raise RuntimeError("upstream is gone")
        return result

    adapter._call_action = _call_action  # type: ignore[method-assign]
    return calls


class TestConnectedBotProfile:
    @pytest.mark.asyncio
    async def test_it_reports_the_upstream_nickname(self):
        adapter = make_adapter()
        _stub_action(adapter, LOGIN_INFO)

        profile = await adapter.get_bot_profile()

        assert profile is not None
        assert profile.user_id == "1726256417"
        assert profile.display_name == "研究助手"
        assert profile.username == "研究助手"

    @pytest.mark.asyncio
    async def test_it_asks_the_upstream_for_login_info(self):
        adapter = make_adapter()
        calls = _stub_action(adapter, LOGIN_INFO)

        await adapter.get_bot_profile()

        assert calls[0]["action"] == "get_login_info"

    @pytest.mark.asyncio
    async def test_the_avatar_url_carries_no_credential(self):
        """头像地址进界面；带上任何令牌或 cookie 都会流出去。"""
        adapter = make_adapter()
        _stub_action(adapter, LOGIN_INFO)

        profile = await adapter.get_bot_profile()

        assert profile is not None
        assert profile.avatar_url is not None
        assert "1726256417" in profile.avatar_url
        for secret in ("token", "cookie", "skey", "auth"):
            assert secret not in profile.avatar_url.lower()

    @pytest.mark.asyncio
    async def test_a_successful_query_latches_the_account_id(self):
        """这是唯一能确认当前登录账号的时机；多账号路由依赖它。"""
        adapter = make_adapter()
        _stub_action(adapter, LOGIN_INFO)

        await adapter.get_bot_profile()

        assert adapter.self_id == "1726256417"

    @pytest.mark.asyncio
    async def test_an_explicit_self_id_is_forwarded(self):
        adapter = make_adapter()
        calls = _stub_action(adapter, LOGIN_INFO)

        await adapter.get_bot_profile(self_id="762211750")

        assert calls[0]["self_id"] == "762211750"


class TestDisconnectedBotProfile:
    @pytest.mark.asyncio
    async def test_it_returns_a_placeholder_instead_of_raising(self):
        """QQ 没登录时这是用户唯一看到的东西；抛错会让整页 500。"""
        adapter = make_adapter()
        _stub_action(adapter, None, raises=True)

        profile = await adapter.get_bot_profile()

        assert profile is not None
        assert profile.display_name == "未连接"

    @pytest.mark.asyncio
    async def test_a_failed_query_does_not_latch_a_placeholder_account_id(self):
        """写回占位值会让后续动作路由到一个不存在的账号。"""
        adapter = make_adapter()
        adapter.self_id = "1726256417"
        _stub_action(adapter, None, raises=True)

        await adapter.get_bot_profile()

        assert adapter.self_id == "1726256417"

    @pytest.mark.asyncio
    async def test_the_placeholder_carries_no_avatar(self):
        """未连接时没有账号，因此没有头像可指——编一个会指向别人的头像。"""
        adapter = make_adapter()
        _stub_action(adapter, None, raises=True)

        profile = await adapter.get_bot_profile()

        assert profile is not None
        assert profile.avatar_url is None
