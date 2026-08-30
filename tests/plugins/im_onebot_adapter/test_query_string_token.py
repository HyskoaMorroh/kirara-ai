"""查询串形式的 access_token 必须真的能连上（需求 11 / C-D 互操作）。

`_classify_access_token` 会读 `?access_token=...` 并在它正确时返回 `None`
（表示「凭据没问题」）。但**被包装的 aiocqhttp 只读 `Authorization` 请求头**
（`aiocqhttp/__init__.py:494-499`：正则匹配 `Token|Bearer <token>`，匹配不上就
`abort(401)`）。于是用查询串认证的实现会被 aiocqhttp 以 401 拒绝，而我们的
分类器认为一切正常、不记录任何原因码——健康面板上既不是「已连接」也没有失败原因，
比不给原因更糟。

正确做法不是把分类器改回去（那等于宣布不支持这种认证方式），而是让它**真的生效**：
在 ASGI 层把已校验的查询串令牌补成一个 `Authorization` 头再交给 aiocqhttp。
LLOneBot 与 NapCat 都允许这种配置，拒绝它没有任何好处。

三条边界：
* 只在**请求头缺失**时补；已有 `Authorization` 头时不得覆盖（那会让「头里是错的、
  查询串里是对的」这种矛盾配置意外通过）。
* 令牌错误时不补——补了就等于把 403 变成 200，绕过认证。
* 补进去的值绝不写日志。
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig


def make_client(*, token: str = "onebot-secret") -> tuple[OneBotAdapter, TestClient, str]:
    config = OneBotConfig(
        websocket_url="/im/websocket/onebot/integration/ws",
        access_token=token,
    )
    adapter = OneBotAdapter(config)
    adapter._started = True
    app = FastAPI()
    app.mount(config.websocket_path, adapter.asgi)
    return adapter, TestClient(app), f"{config.websocket_path}/ws"


def wait_for_connection(adapter: OneBotAdapter, account_id: str) -> None:
    deadline = time.monotonic() + 1
    while account_id not in adapter.connections and time.monotonic() < deadline:
        time.sleep(0.01)


def test_a_correct_query_string_token_is_accepted_and_connects():
    """`?access_token=<正确值>` 且无 Authorization 头时必须连上。

    此前 aiocqhttp 会 401，而适配器记录的原因是 `None`——面板上什么都没有。
    """
    adapter, client, websocket_path = make_client()

    with client, client.websocket_connect(
        f"{websocket_path}?access_token=onebot-secret",
        headers={"X-Client-Role": "universal", "X-Self-ID": "10001"},
    ) as websocket:
        websocket.send_json(
            {
                "time": 1,
                "self_id": 10001,
                "post_type": "meta_event",
                "meta_event_type": "lifecycle",
                "sub_type": "connect",
            }
        )
        wait_for_connection(adapter, "10001")

        assert adapter.get_health_snapshot().status == "connected"
        assert adapter.self_id == "10001"


def test_a_wrong_query_string_token_is_still_rejected():
    """令牌错误时不得补头：补了就等于把 403 变成 200，绕过认证。"""
    adapter, client, websocket_path = make_client()

    with client, pytest.raises(Exception) as rejected:
        with client.websocket_connect(
            f"{websocket_path}?access_token=wrong-token",
            headers={"X-Client-Role": "universal", "X-Self-ID": "10001"},
        ):
            pass

    assert getattr(rejected.value, "status_code", None) in {401, 403}
    assert adapter.get_health_snapshot().last_disconnect_reason == "access_token_mismatch"


def test_a_missing_token_in_both_places_is_rejected_with_a_reason():
    adapter, client, websocket_path = make_client()

    with client, pytest.raises(Exception) as rejected:
        with client.websocket_connect(
            websocket_path,
            headers={"X-Client-Role": "universal", "X-Self-ID": "10001"},
        ):
            pass

    assert getattr(rejected.value, "status_code", None) == 401
    assert adapter.get_health_snapshot().last_disconnect_reason == "access_token_missing"


def test_an_existing_authorization_header_is_never_overwritten():
    """头里是错的、查询串里是对的——这是矛盾配置，必须以头为准而被拒绝。

    覆盖它会让一个配错的部署意外「能用」，之后换个客户端又突然不能用。
    """
    adapter, client, websocket_path = make_client()

    with client, pytest.raises(Exception) as rejected:
        with client.websocket_connect(
            f"{websocket_path}?access_token=onebot-secret",
            headers={
                "Authorization": "Bearer wrong-token",
                "X-Client-Role": "universal",
                "X-Self-ID": "10001",
            },
        ):
            pass

    assert getattr(rejected.value, "status_code", None) in {401, 403}


def test_no_token_configured_leaves_the_query_string_alone():
    """未配置令牌时不做任何注入，行为与从前完全一致。"""
    adapter, client, websocket_path = make_client(token="")

    with client, client.websocket_connect(
        f"{websocket_path}?access_token=whatever",
        headers={"X-Client-Role": "universal", "X-Self-ID": "10001"},
    ) as websocket:
        websocket.send_json(
            {
                "time": 1,
                "self_id": 10001,
                "post_type": "meta_event",
                "meta_event_type": "lifecycle",
                "sub_type": "connect",
            }
        )
        wait_for_connection(adapter, "10001")

        assert adapter.get_health_snapshot().status == "connected"
