import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

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


@pytest.mark.parametrize("role", ["event", "universal"])
def test_reverse_websocket_accepts_event_roles_and_updates_health(role: str):
    adapter, client, websocket_path = make_client()

    with client, client.websocket_connect(
        websocket_path,
        headers={
            "Authorization": "Bearer onebot-secret",
            "X-Client-Role": role,
            "X-Self-ID": "10001",
        },
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


def test_reverse_websocket_accepts_api_role():
    _, client, websocket_path = make_client()

    with client, client.websocket_connect(
        websocket_path,
        headers={
            "Authorization": "Token onebot-secret",
            "X-Client-Role": "api",
            "X-Self-ID": "10001",
        },
    ):
        pass


@pytest.mark.parametrize(
    ("authorization", "expected_status"),
    [(None, 401), ("Bearer wrong-token", 403)],
)
def test_reverse_websocket_rejects_missing_or_invalid_token(
    authorization: str | None, expected_status: int
):
    _, client, websocket_path = make_client()
    headers = {"X-Client-Role": "universal", "X-Self-ID": "10001"}
    if authorization:
        headers["Authorization"] = authorization

    with client, pytest.raises(Exception) as rejected:
        with client.websocket_connect(websocket_path, headers=headers):
            pass

    assert getattr(rejected.value, "status_code", None) == expected_status


@pytest.mark.parametrize("role", [None, "", "unsupported"])
def test_reverse_websocket_rejects_missing_or_invalid_client_role(role: str | None):
    _, client, websocket_path = make_client()
    headers = {"Authorization": "Bearer onebot-secret"}
    if role is not None:
        headers["X-Client-Role"] = role

    with client, pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect(websocket_path, headers=headers):
            pass

    assert rejected.value.code == 4400


@pytest.mark.parametrize("role", ["api", "universal"])
def test_reverse_websocket_rejects_api_roles_without_self_id(role: str):
    _, client, websocket_path = make_client()

    with client, pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect(
            websocket_path,
            headers={
                "Authorization": "Bearer onebot-secret",
                "X-Client-Role": role,
            },
        ):
            pass

    assert rejected.value.code == 4400
