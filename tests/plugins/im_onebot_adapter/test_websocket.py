import asyncio
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


@pytest.mark.asyncio
async def test_asgi_treats_cancellation_after_disconnect_as_normal_close():
    adapter, _, _ = make_client()

    class DisconnectingBot:
        async def asgi(self, scope, receive, send):
            assert await receive() == {"type": "websocket.disconnect"}
            raise asyncio.CancelledError

    adapter.bot = DisconnectingBot()  # type: ignore[assignment]

    async def receive():
        return {"type": "websocket.disconnect"}

    await adapter.asgi(
        {
            "type": "websocket",
            "headers": [
                (b"x-client-role", b"event"),
                (b"x-self-id", b"10001"),
            ],
        },
        receive,
        lambda message: None,
    )


@pytest.mark.asyncio
async def test_asgi_treats_cancellation_with_queued_disconnect_as_normal_close():
    adapter, _, _ = make_client()
    receive_queue = asyncio.Queue()
    receive_started = asyncio.Event()
    receive_calls = 0

    class DisconnectingBot:
        async def asgi(self, scope, receive, send):
            assert await receive() == {"type": "websocket.connect"}
            await receive()

    adapter.bot = DisconnectingBot()  # type: ignore[assignment]

    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            return {"type": "websocket.connect"}
        if receive_calls == 2:
            receive_started.set()
            await asyncio.Future()
        return receive_queue.get_nowait()

    task = asyncio.create_task(
        adapter.asgi(
            {
                "type": "websocket",
                "headers": [
                    (b"x-client-role", b"event"),
                    (b"x-self-id", b"10001"),
                ],
            },
            receive,
            lambda message: None,
        )
    )
    await asyncio.wait_for(receive_started.wait(), timeout=1)
    receive_queue.put_nowait({"type": "websocket.disconnect", "code": 1000})
    task.cancel()
    await task

    assert receive_calls == 3


@pytest.mark.asyncio
async def test_asgi_propagates_cancellation_without_disconnect():
    adapter, _, _ = make_client()

    class CancelledBot:
        async def asgi(self, scope, receive, send):
            raise asyncio.CancelledError

    adapter.bot = CancelledBot()  # type: ignore[assignment]

    async def receive():
        return {"type": "websocket.connect"}

    with pytest.raises(asyncio.CancelledError):
        await adapter.asgi(
            {
                "type": "websocket",
                "headers": [
                    (b"x-client-role", b"event"),
                    (b"x-self-id", b"10001"),
                ],
            },
            receive,
            lambda message: None,
        )


@pytest.mark.asyncio
async def test_asgi_propagates_business_errors_after_disconnect():
    adapter, _, _ = make_client()

    class FailingBot:
        async def asgi(self, scope, receive, send):
            assert await receive() == {"type": "websocket.disconnect"}
            raise RuntimeError("handler failure")

    adapter.bot = FailingBot()  # type: ignore[assignment]

    async def receive():
        return {"type": "websocket.disconnect"}

    with pytest.raises(RuntimeError, match="handler failure"):
        await adapter.asgi(
            {
                "type": "websocket",
                "headers": [
                    (b"x-client-role", b"event"),
                    (b"x-self-id", b"10001"),
                ],
            },
            receive,
            lambda message: None,
        )
