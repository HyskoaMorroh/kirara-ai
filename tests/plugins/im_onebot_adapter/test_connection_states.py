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
