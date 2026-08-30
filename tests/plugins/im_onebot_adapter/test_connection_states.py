"""Connection-state regression tests for the OneBot reverse WebSocket.

The shipped health model had four statuses (connected / waiting / disconnected /
stale) and no disconnect reason. During a `docker compose down && pull && up -d`
cycle that made five genuinely different situations indistinguishable in the UI:

- the container is up but the adapter has not finished starting;
- the adapter is mounted and the OneBot implementation has simply not dialed in yet;
- the implementation dialed in with a wrong or missing access token;
- the implementation dialed in with malformed handshake headers;
- the implementation was connected and the link went stale.

Only the middle three were ever surfaced, all as "waiting", so "QQ shows
disconnected after a restart" could not be diagnosed from the panel.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig


def make_adapter(config: OneBotConfig | None = None) -> OneBotAdapter:
    container = DependencyContainer()
    container.register(OneBotConfig, config or OneBotConfig())
    return Inject(container).create(OneBotAdapter)()


def make_client(token: str = "onebot-secret") -> tuple[OneBotAdapter, TestClient, str]:
    config = OneBotConfig(
        websocket_url="/im/websocket/onebot/state/ws",
        access_token=token,
    )
    adapter = make_adapter(config)
    adapter._started = True
    app = FastAPI()
    app.mount(config.websocket_path, adapter.asgi)
    return adapter, TestClient(app), f"{config.websocket_path}/ws"


def test_status_is_initializing_before_start_completes():
    adapter = make_adapter()

    # Never started: this is the container-just-came-up case, which used to be
    # reported with the same word as a real failure.
    snapshot = adapter.get_health_snapshot()

    assert snapshot.status == "initializing"
    assert snapshot.adapter_started is False
    assert snapshot.websocket_connected is False


def test_status_is_waiting_once_mounted_with_no_client_yet():
    adapter = make_adapter()
    adapter._started = True

    snapshot = adapter.get_health_snapshot()

    assert snapshot.status == "waiting"
    assert snapshot.last_disconnect_reason is None


def test_status_is_disconnected_after_a_clean_stop():
    adapter = make_adapter()
    adapter._started = True
    adapter.connections["100"] = {"last_heartbeat": 1.0}
    assert adapter.get_health_snapshot(now=1.0).status == "connected"

    adapter._started = False

    assert adapter.get_health_snapshot(now=1.0).status == "disconnected"


def test_credential_rejection_is_a_distinct_state_with_a_reason():
    adapter, client, websocket_path = make_client()

    with client, pytest.raises(Exception):
        with client.websocket_connect(
            websocket_path,
            headers={
                "Authorization": "Bearer wrong-token",
                "X-Client-Role": "universal",
                "X-Self-ID": "10001",
            },
        ):
            pass

    snapshot = adapter.get_health_snapshot()
    assert snapshot.status == "credential_rejected"
    assert snapshot.last_disconnect_reason == "access_token_mismatch"


def test_missing_credential_is_reported_as_credential_rejected():
    adapter, client, websocket_path = make_client()

    with client, pytest.raises(Exception):
        with client.websocket_connect(
            websocket_path,
            headers={"X-Client-Role": "universal", "X-Self-ID": "10001"},
        ):
            pass

    snapshot = adapter.get_health_snapshot()
    assert snapshot.status == "credential_rejected"
    assert snapshot.last_disconnect_reason == "access_token_missing"


def test_malformed_handshake_is_reported_as_upstream_refused():
    adapter, client, websocket_path = make_client()

    with client, pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            websocket_path,
            headers={"Authorization": "Bearer onebot-secret", "X-Client-Role": "nonsense"},
        ):
            pass

    snapshot = adapter.get_health_snapshot()
    assert snapshot.status == "upstream_refused"
    assert snapshot.last_disconnect_reason == "invalid_client_role"


def test_missing_self_id_for_api_role_is_reported_with_its_own_reason():
    adapter, client, websocket_path = make_client()

    with client, pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            websocket_path,
            headers={"Authorization": "Bearer onebot-secret", "X-Client-Role": "api"},
        ):
            pass

    assert adapter.get_health_snapshot().last_disconnect_reason == "missing_self_id"


def test_a_successful_connection_clears_a_previous_failure_reason():
    adapter, client, websocket_path = make_client()

    with client, pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            websocket_path,
            headers={"Authorization": "Bearer onebot-secret", "X-Client-Role": "nonsense"},
        ):
            pass
    assert adapter.get_health_snapshot().status == "upstream_refused"

    adapter.connections["10001"] = {"last_heartbeat": 5.0}
    adapter._connection_status = "connected"

    snapshot = adapter.get_health_snapshot(now=5.0)
    assert snapshot.status == "connected"
    assert snapshot.last_disconnect_reason is None


def test_a_stale_link_keeps_reporting_stale_with_a_heartbeat_reason():
    adapter = make_adapter(OneBotConfig(heartbeat_interval=15))
    adapter._started = True
    adapter.connections["100"] = {"last_heartbeat": 100.0}

    adapter._prune_stale_connections(now=190.1)
    snapshot = adapter.get_health_snapshot(now=190.1)

    assert snapshot.status == "stale"
    assert snapshot.last_disconnect_reason == "heartbeat_timeout"


def test_upstream_reported_disconnect_keeps_the_lifecycle_reason():
    adapter = make_adapter()
    adapter._started = True
    adapter.connections["100"] = {"last_heartbeat": 1.0}

    adapter._record_connection_failure("upstream_lifecycle_disconnect", status=None)
    adapter.connections.clear()
    adapter._connection_status = "disconnected"

    snapshot = adapter.get_health_snapshot(now=1.0)
    assert snapshot.status == "disconnected"
    assert snapshot.last_disconnect_reason == "upstream_lifecycle_disconnect"


def test_access_token_in_the_query_string_is_accepted():
    """查询参数形式的 Token 必须与请求头等价。

    LLOneBot 与 NapCat 都允许 `?access_token=...`。只读请求头时这类连接会被记成
    `access_token_missing`，而 aiocqhttp 实际放行了——健康面板给出的原因码与
    真实情况相反，比不给原因更糟。
    """
    adapter = object.__new__(OneBotAdapter)
    adapter.config = OneBotConfig(access_token="s3cret")

    assert adapter._classify_access_token({}, b"access_token=s3cret") is None


def test_a_wrong_query_string_token_is_reported_as_a_mismatch():
    adapter = object.__new__(OneBotAdapter)
    adapter.config = OneBotConfig(access_token="s3cret")

    assert (
        adapter._classify_access_token({}, b"access_token=wrong")
        == "access_token_mismatch"
    )


def test_a_header_token_still_wins_over_the_query_string():
    """请求头优先：两者都给且请求头正确时不应因查询串而误判。"""
    adapter = object.__new__(OneBotAdapter)
    adapter.config = OneBotConfig(access_token="s3cret")

    assert (
        adapter._classify_access_token(
            {"authorization": "Bearer s3cret"}, b"access_token=stale"
        )
        is None
    )


def test_no_token_anywhere_is_still_reported_as_missing():
    adapter = object.__new__(OneBotAdapter)
    adapter.config = OneBotConfig(access_token="s3cret")

    assert adapter._classify_access_token({}, b"") == "access_token_missing"


def test_a_malformed_query_string_is_treated_as_no_token():
    """畸形查询串不得抛错：那会让握手在观测代码里失败。"""
    adapter = object.__new__(OneBotAdapter)
    adapter.config = OneBotConfig(access_token="s3cret")

    assert (
        adapter._classify_access_token({}, b"\xff\xfe not a query")
        == "access_token_missing"
    )


def test_health_snapshot_omits_qr_login_when_no_log_path_is_configured():
    """未配置日志路径时必须是 None，而不是一个空快照。

    「没开这个功能」和「开了但读不到事件」是两件事：后者说明路径配错或日志没写，
    前者是正常的默认状态。用空快照表示前者会让操作者去排查一个不存在的问题。
    """
    adapter = make_adapter()

    assert adapter.get_health_snapshot(now=0).qr_login is None


def test_health_snapshot_folds_the_upstream_log_into_a_qr_snapshot(tmp_path):
    """配了路径就把上游日志折成可回答的扫码状态。"""
    log = tmp_path / "llonebot.log"
    log.write_text(
        "[2026-08-20T21:37:19Z INFO pmhq] listener.onQRCodeGetPicture expireTime= 120 urlLen= 68\n"
        "[2026-08-20T21:37:20Z INFO pmhq] [I] qq-protocol 二维码文件已保存: /root/llonebot/data/temp/login-qrcode.png\n",
        encoding="utf-8",
    )
    adapter = make_adapter(OneBotConfig(qr_login_log_path=str(log)))

    snapshot = adapter.get_health_snapshot(now=0).qr_login

    assert snapshot is not None
    assert snapshot.state in {"waiting_scan", "expired"}
    assert snapshot.validity_seconds == 120
    assert snapshot.latest_qr_path.endswith("login-qrcode.png")


def test_an_unreadable_log_never_breaks_the_health_snapshot(tmp_path):
    """日志读不到只应失去这一项，不能让整个健康快照失败。

    观测不能成为新的失败点：连接面板必须仍然能回答「适配器连上了吗」。
    """
    adapter = make_adapter(
        OneBotConfig(qr_login_log_path=str(tmp_path / "does-not-exist.log"))
    )

    snapshot = adapter.get_health_snapshot(now=0)

    assert snapshot.qr_login is None
    assert snapshot.status in {"initializing", "waiting", "disconnected"}


def test_only_the_log_tail_is_read(tmp_path):
    """只读尾部：长期运行的部署上整文件读取会把诊断接口变成慢查询。"""
    log = tmp_path / "big.log"
    filler = "[2026-08-20T20:00:00Z INFO pmhq] noise line\n" * 4000
    log.write_text(
        filler
        + "[2026-08-20T21:37:19Z INFO pmhq] listener.onQRCodeGetPicture expireTime= 120 urlLen= 68\n",
        encoding="utf-8",
    )
    adapter = make_adapter(
        OneBotConfig(qr_login_log_path=str(log), qr_login_log_tail_bytes=2048)
    )

    snapshot = adapter.get_health_snapshot(now=0).qr_login

    assert snapshot is not None
    # 尾部足以覆盖最后那条事件，因此状态仍然可解读。
    assert snapshot.validity_seconds == 120


def test_the_qr_snapshot_in_health_carries_no_account_identifiers(tmp_path):
    """健康快照会被前端与 readiness 接口直接返回，绝不能带账号标识。

    夹具用合成账号标识：把真实 uin / uid 写进测试本身就违反「私有数据不入源码」
    这条约束，即使这条测试断言的正是「不泄露」。
    """
    log = tmp_path / "llonebot.log"
    log.write_text(
        "[2026-08-20T21:37:19Z INFO pmhq] listener.onQRCodeGetPicture expireTime= 120 urlLen= 68\n"
        '[2026-08-20T21:37:39Z INFO pmhq] listener.onQRCodeLoginSucceed {"account":"1000000001",'
        '"uid":"u_SyntheticUidForTestOnly","nickName":"测试账号"}\n',
        encoding="utf-8",
    )
    adapter = make_adapter(OneBotConfig(qr_login_log_path=str(log)))

    serialized = adapter.get_health_snapshot(now=0).model_dump_json()

    for leaked in ("1000000001", "u_SyntheticUidForTestOnly", "测试账号"):
        assert leaked not in serialized
