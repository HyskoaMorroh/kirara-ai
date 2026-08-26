import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import kirara_ai.plugins.im_qqbot_adapter.adapter as adapter_module
from kirara_ai.plugins.im_qqbot_adapter.adapter import QQBotAdapter, QQBotConfig


class FakeWebServer:
    def __init__(self):
        self.app = SimpleNamespace(routes=[])

    def mount_app(self, prefix, app):
        self.app.routes.append(SimpleNamespace(path=prefix, app=app))


class FakeBotWebHook:
    def __init__(self, *_args, **_kwargs):
        self.app = object()

    async def init_fastapi(self):
        return SimpleNamespace(user_middleware=[object()])


def _adapter(*, path: str = "/im/webhook/qqbot/test/") -> QQBotAdapter:
    adapter = object.__new__(QQBotAdapter)
    adapter.config = QQBotConfig(
        app_id="app-id",
        app_secret="super-secret-app-secret",
        token="super-secret-token",
        webhook_url=path,
    )
    adapter.logger = MagicMock()
    adapter.http = SimpleNamespace(
        login=AsyncMock(
            return_value={
                "id": "123456",
                "username": "research-bot",
                "avatar": "https://example.invalid/avatar.png",
            }
        ),
        close=AsyncMock(),
    )
    adapter.api = object()
    adapter.loop = asyncio.get_running_loop()
    adapter.web_server = FakeWebServer()
    adapter.user = None
    adapter.robot = None
    adapter._mount_path = None
    adapter._mounted_route = None
    adapter._started = False
    return adapter


def test_qqbot_config_redacts_credentials_from_string_representations():
    config = QQBotConfig(
        app_id="app-id",
        app_secret="super-secret-app-secret",
        token="super-secret-token",
    )

    rendered = f"{config!r} {config}"
    assert "super-secret-app-secret" not in rendered
    assert "super-secret-token" not in rendered
    assert "redacted" in rendered.lower()


@pytest.mark.asyncio
async def test_start_mounts_one_owned_route_and_reports_webhook_readiness(monkeypatch):
    monkeypatch.setattr(adapter_module.botpy, "BotWebHook", FakeBotWebHook)
    adapter = _adapter()

    await adapter.start()

    assert [route.path for route in adapter.web_server.app.routes] == [
        "/im/webhook/qqbot/test"
    ]
    assert adapter._mounted_route is adapter.web_server.app.routes[0]
    health = adapter.get_health_snapshot()
    assert health.status == "connected"
    assert health.connected_account_count == 1
    assert health.adapter_started is True


@pytest.mark.asyncio
async def test_start_rejects_duplicate_webhook_path_before_login(monkeypatch):
    monkeypatch.setattr(adapter_module.botpy, "BotWebHook", FakeBotWebHook)
    adapter = _adapter()
    existing = SimpleNamespace(path="/im/webhook/qqbot/test", app=object())
    adapter.web_server.app.routes.append(existing)

    with pytest.raises(RuntimeError, match="路径已被占用"):
        await adapter.start()

    adapter.http.login.assert_not_awaited()
    assert adapter.web_server.app.routes == [existing]


@pytest.mark.asyncio
async def test_stop_unmounts_owned_route_closes_http_and_is_idempotent(monkeypatch):
    monkeypatch.setattr(adapter_module.botpy, "BotWebHook", FakeBotWebHook)
    adapter = _adapter()
    await adapter.start()

    await adapter.stop()
    await adapter.stop()

    assert adapter.web_server.app.routes == []
    adapter.http.close.assert_awaited_once_with()
    assert adapter.user is None
    assert adapter.robot is None
    assert adapter.get_health_snapshot().status == "disconnected"


@pytest.mark.asyncio
async def test_stop_propagates_cancellation_after_unmounting_route(monkeypatch):
    monkeypatch.setattr(adapter_module.botpy, "BotWebHook", FakeBotWebHook)
    adapter = _adapter()
    await adapter.start()
    adapter.http.close.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await adapter.stop()

    assert adapter.web_server.app.routes == []
    assert adapter.get_health_snapshot().status == "disconnected"


@pytest.mark.asyncio
async def test_stop_logs_and_propagates_http_close_failure(monkeypatch):
    monkeypatch.setattr(adapter_module.botpy, "BotWebHook", FakeBotWebHook)
    adapter = _adapter()
    await adapter.start()
    adapter.http.close.side_effect = OSError("QQ HTTP close failed")

    with pytest.raises(OSError, match="QQ HTTP close failed"):
        await adapter.stop()

    adapter.logger.opt.assert_called_once_with(exception=True)
    adapter.logger.opt.return_value.error.assert_called_once()
    assert adapter.web_server.app.routes == []
