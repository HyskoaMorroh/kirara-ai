"""创建者渠道身份必须能从界面配置（需求 10）。

`creator_channel_identities` 决定 IM 渠道上谁能用受保护的插件能力
（MCP 工具、command Hook）。它默认是空表，含义是"聊天侧谁都拿不到创建者身份"，
于是 `principal_can_control_agent` 恒假、MCP 工具列表恒空——**包括创建者本人**。

`global_config.py` 里那段类注释自己承认了这个形态：
「结果不是『非创建者不行』，而是『所有人都不行』」。桥已经架好了，但唯一的
配置入口是手改 `config.yaml`：`GET/POST /system/config/agent_runtime` 的可写键
集合里没有它，WebUI 也没有编辑面板。

后果是需求 10 的前半句在默认部署下无法成立：用户在 QQ / Telegram 里对着自己的
机器人说"帮我装个 skill"，得到的是一次正常回复但工具一个都没生效，而界面上
没有任何地方解释为什么。他必须先知道有这个字段、再登服务器改 YAML、再重启。

这里锁住：字段可读、可写、有边界校验，并且校验要覆盖「群聊默认不生效」这条
刻意的安全默认——它是防止把宿主操作暴露在多人可见会话里的唯一开关。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.config.global_config import CreatorChannelIdentity, GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


def _make_api(tmp_path: Path, *, config: GlobalConfig | None = None):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, config or GlobalConfig())
    container.register(
        AuthService, MockAuthService(scopes=["*"], subject="creator")
    )
    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), container.resolve(GlobalConfig)


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


@pytest.mark.asyncio
async def test_the_identities_are_readable_from_the_config_endpoint(tmp_path: Path):
    """界面要能显示当前声明了哪些身份，否则用户无从判断为什么工具不可用。"""
    config = GlobalConfig()
    config.agent_runtime.creator_channel_identities = [
        CreatorChannelIdentity(channel_type="onebot", sender_scope="10001")
    ]
    client, _ = _make_api(tmp_path, config=config)

    response = await client.get("/api/system/config", headers=_headers())
    payload = await response.get_json()

    assert response.status_code == 200
    identities = payload["agent_runtime"]["creator_channel_identities"]
    assert identities == [
        {
            "channel_type": "onebot",
            "sender_scope": "10001",
            "account_scope": None,
            "adapter_instance": None,
            "allow_group_chat": False,
        }
    ]


@pytest.mark.asyncio
async def test_identities_can_be_written_through_the_api(tmp_path: Path, monkeypatch):
    """能写才算有入口。只读不可写等于仍然要登服务器改 YAML。"""
    saved: list[GlobalConfig] = []
    monkeypatch.setattr(
        "kirara_ai.web.api.system.routes.ConfigLoader.save_config_with_backup",
        lambda _path, cfg: saved.append(cfg),
    )
    client, config = _make_api(tmp_path)

    response = await client.post(
        "/api/system/config/agent-runtime",
        headers=_headers(),
        json={
            "creator_channel_identities": [
                {
                    "channel_type": "telegram",
                    "sender_scope": "424242",
                    "allow_group_chat": True,
                }
            ]
        },
    )

    assert response.status_code == 200
    assert saved, "配置没有落盘"
    stored = config.agent_runtime.creator_channel_identities
    assert len(stored) == 1
    assert stored[0].channel_type == "telegram"
    assert stored[0].sender_scope == "424242"
    assert stored[0].allow_group_chat is True


@pytest.mark.asyncio
async def test_an_unsupported_channel_type_is_rejected(tmp_path: Path):
    """渠道名写错不能静默落盘——那会让这条身份永远匹配不上任何消息。"""
    client, config = _make_api(tmp_path)

    response = await client.post(
        "/api/system/config/agent-runtime",
        headers=_headers(),
        json={
            "creator_channel_identities": [
                {"channel_type": "not-a-channel", "sender_scope": "1"}
            ]
        },
    )

    assert response.status_code == 400
    assert config.agent_runtime.creator_channel_identities == []


@pytest.mark.asyncio
async def test_an_empty_sender_scope_is_rejected(tmp_path: Path):
    """空的发送者标识会匹配上谁？这个问题不该留给运行时回答。"""
    client, config = _make_api(tmp_path)

    response = await client.post(
        "/api/system/config/agent-runtime",
        headers=_headers(),
        json={
            "creator_channel_identities": [
                {"channel_type": "onebot", "sender_scope": "   "}
            ]
        },
    )

    assert response.status_code == 400
    assert config.agent_runtime.creator_channel_identities == []


@pytest.mark.asyncio
async def test_group_chat_stays_off_unless_explicitly_enabled(tmp_path: Path, monkeypatch):
    """`allow_group_chat` 缺省必须是 False。

    群里所有人都看得到创建者发的指令并照抄。照抄的人 `sender_scope` 不同因而
    拿不到身份，但把宿主操作暴露在多人可见的会话里是另一回事——这个默认值是
    那道刻意的门，不能因为「字段没填」就自动打开。
    """
    monkeypatch.setattr(
        "kirara_ai.web.api.system.routes.ConfigLoader.save_config_with_backup",
        lambda _path, _cfg: None,
    )
    client, config = _make_api(tmp_path)

    response = await client.post(
        "/api/system/config/agent-runtime",
        headers=_headers(),
        json={
            "creator_channel_identities": [
                {"channel_type": "wecom", "sender_scope": "creator-user"}
            ]
        },
    )

    assert response.status_code == 200
    assert config.agent_runtime.creator_channel_identities[0].allow_group_chat is False


@pytest.mark.asyncio
async def test_an_empty_list_clears_the_declaration(tmp_path: Path, monkeypatch):
    """要能撤销。声明了之后无法清空，等于这个开关只有单向。"""
    monkeypatch.setattr(
        "kirara_ai.web.api.system.routes.ConfigLoader.save_config_with_backup",
        lambda _path, _cfg: None,
    )
    config = GlobalConfig()
    config.agent_runtime.creator_channel_identities = [
        CreatorChannelIdentity(channel_type="onebot", sender_scope="10001")
    ]
    client, live = _make_api(tmp_path, config=config)

    response = await client.post(
        "/api/system/config/agent-runtime",
        headers=_headers(),
        json={"creator_channel_identities": []},
    )

    assert response.status_code == 200
    assert live.agent_runtime.creator_channel_identities == []


@pytest.mark.asyncio
async def test_omitting_the_key_keeps_the_existing_declaration(tmp_path: Path, monkeypatch):
    """没提交这个键时保留原值——与该端点其余字段的 `exclude_unset` 语义一致。

    「改一个字段把其余重置回出厂值」在这个仓库的容错字段上真实发生过，
    不能在这里重挖一遍：把创建者身份清空的后果是聊天侧插件能力全部失效。
    """
    monkeypatch.setattr(
        "kirara_ai.web.api.system.routes.ConfigLoader.save_config_with_backup",
        lambda _path, _cfg: None,
    )
    config = GlobalConfig()
    config.agent_runtime.creator_channel_identities = [
        CreatorChannelIdentity(channel_type="qqbot", sender_scope="90001")
    ]
    client, live = _make_api(tmp_path, config=config)

    response = await client.post(
        "/api/system/config/agent-runtime",
        headers=_headers(),
        json={"tool_search_threshold": 20},
    )

    assert response.status_code == 200
    assert len(live.agent_runtime.creator_channel_identities) == 1
    assert live.agent_runtime.creator_channel_identities[0].sender_scope == "90001"
