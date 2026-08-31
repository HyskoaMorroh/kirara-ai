"""扫码状态必须有一个「刷新」动作，而不只能等下一次列表轮询。

需求 18.4 逐项点名：有效期、生成时间、当前状态、**刷新动作**、失败原因、
最新二维码路径。前五项里只有「刷新动作」缺失——`qr_login` 快照只在
`GET /im/adapters` 与 `GET /im/adapters/<id>` 里随整份适配器信息返回，
没有任何单独的入口。

这一条不是锦上添花。二维码有效期实测 120 秒，而适配器列表的轮询间隔远长于
「这张码还剩几秒」这个问题的时间尺度。操作者的真实动作序列是：

1. 打开面板，看到一张码；
2. 走去拿手机；
3. 回来扫——此时屏幕上那张已经过期，上游其实早就生成了新的。

而他无法知道第 3 步发生了：面板上的数字是上一次轮询的快照。这正是
「二维码总是过期，无法登录」这个报障的形态。

**刷新的语义必须写准。** Kirara 不生成二维码，LLOneBot / PMHQ 在自己的容器里
生成。刷新做的是「立刻重读上游日志，给出最新一张码的状态」，不是
「让上游重新生成一张」。把后者写进按钮文案是对所有权的谎报：点了没反应时，
操作者会去排查 Kirara，而要看的是上游容器。

另外两条边界：
- **未配置日志路径时返回明确的「未启用」而不是空快照。**「没开这个功能」
  与「开了但读不到任何事件」是两件事，混成一个会让人去查挂载。
- **不返回二维码内容本身。** 二维码是登录凭据材料；状态面板不该成为
  它流经的地方。路径已经在快照里，扫码在上游 WebUI 完成。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from kirara_ai.config.global_config import GlobalConfig, IMConfig, WebConfig
from kirara_ai.im.manager import IMManager
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.plugin_manager.plugin_loader import PluginLoader
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from tests.utils.auth_test_utils import auth_headers, setup_auth_service  # noqa

TEST_SECRET_KEY = "test-secret-key"

ADAPTER_ID = "onebot-main"
ENDPOINT = f"/backend-api/api/im/adapters/{ADAPTER_ID}/qr-login"


def _snapshot(**overrides):
    from kirara_ai.im.qr_login import QRLoginSnapshot

    payload = {
        "state": "waiting_scan",
        "validity_seconds": 120,
        "remaining_seconds": 92.0,
        "latest_qr_path": "/root/llonebot/data/temp/login-qrcode.png",
        "refresh_count": 2,
    }
    payload.update(overrides)
    return QRLoginSnapshot(**payload)


class _QRAdapter:
    """An adapter whose QR snapshot changes between reads, like the real one."""

    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self.read_count = 0

    def _read_qr_login_snapshot(self):
        self.read_count += 1
        index = min(self.read_count - 1, len(self._snapshots) - 1)
        return self._snapshots[index]


class _PlainAdapter:
    """An adapter with no QR concept at all (Telegram, WeCom, ...)."""


@pytest.fixture
def container() -> DependencyContainer:
    container = DependencyContainer()
    config = GlobalConfig()
    config.web = WebConfig(
        secret_key=TEST_SECRET_KEY, password_file="test_password.hash"
    )
    config.ims = [IMConfig(name=ADAPTER_ID, adapter="onebot", config={}, enable=True)]
    container.register(GlobalConfig, config)
    setup_auth_service(container)

    im_manager = MagicMock(spec=IMManager)
    im_manager.adapters = {}
    container.register(IMManager, im_manager)

    llm_manager = MagicMock(spec=LLMManager)
    llm_manager.active_backends = {}
    container.register(LLMManager, llm_manager)

    plugin_loader = MagicMock(spec=PluginLoader)
    plugin_loader.plugins = []
    container.register(PluginLoader, plugin_loader)

    workflow_registry = MagicMock(spec=WorkflowRegistry)
    workflow_registry.snapshot_builders.return_value = ()
    container.register(WorkflowRegistry, workflow_registry)

    web_server = WebServer(container)
    container.register(WebServer, web_server)
    return container


@pytest.fixture
def test_client(container) -> TestClient:
    return TestClient(container.resolve(WebServer).app)


class TestRefreshReturnsTheNewestSnapshot:
    def test_refresh_rereads_the_upstream_log(
        self, test_client, auth_headers, container
    ):
        fresh = _snapshot(remaining_seconds=118.0, refresh_count=3)
        adapter = _QRAdapter([_snapshot(), fresh])
        manager = container.resolve(IMManager)
        manager.adapters = {ADAPTER_ID: adapter}

        first = test_client.post(ENDPOINT, headers=auth_headers)
        second = test_client.post(ENDPOINT, headers=auth_headers)

        assert first.status_code == 200
        assert second.status_code == 200
        # 每次刷新都必须真的重读一次：返回缓存等于让「刷新」按钮不做事，
        # 而那正是这个动作要解决的问题。
        assert adapter.read_count == 2
        assert second.json()["qr_login"]["refresh_count"] == 3
        assert second.json()["qr_login"]["remaining_seconds"] == 118.0

    def test_response_carries_the_six_named_fields(
        self, test_client, auth_headers, container
    ):
        container.resolve(IMManager).adapters = {ADAPTER_ID: _QRAdapter([_snapshot()])}

        payload = test_client.post(ENDPOINT, headers=auth_headers).json()["qr_login"]

        for field in (
            "state",
            "generated_at",
            "expires_at",
            "validity_seconds",
            "remaining_seconds",
            "latest_qr_path",
            "failure_reason",
        ):
            assert field in payload, f"刷新结果缺少 {field}"

    def test_expired_snapshot_is_reported_as_expired(
        self, test_client, auth_headers, container
    ):
        expired = _snapshot(
            state="expired", remaining_seconds=0.0, failure_reason="expired_without_scan"
        )
        container.resolve(IMManager).adapters = {ADAPTER_ID: _QRAdapter([expired])}

        payload = test_client.post(ENDPOINT, headers=auth_headers).json()["qr_login"]

        # 继续把死码显示成有效是「二维码总是过期」这个报障的另一半。
        assert payload["state"] == "expired"
        assert payload["failure_reason"] == "expired_without_scan"

    def test_refresh_does_not_return_qr_content(
        self, test_client, auth_headers, container
    ):
        container.resolve(IMManager).adapters = {ADAPTER_ID: _QRAdapter([_snapshot()])}

        body = test_client.post(ENDPOINT, headers=auth_headers).json()

        # 二维码是登录凭据材料；状态面板不该成为它流经的地方。
        serialized = str(body)
        assert "data:image" not in serialized
        assert "base64" not in serialized


class TestUnavailableCasesAreDistinguished:
    def test_missing_log_path_says_not_enabled(
        self, test_client, auth_headers, container
    ):
        container.resolve(IMManager).adapters = {ADAPTER_ID: _QRAdapter([None])}

        response = test_client.post(ENDPOINT, headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        # 「没开这个功能」与「开了但读不到任何事件」是两件事：
        # 把前者显示成后者会让人去查挂载，而要做的是填一个配置项。
        assert body["qr_login"] is None
        assert body["supported"] is True
        assert "qr_login_log_path" in body["remediation"]

    def test_adapter_without_qr_support_says_so(
        self, test_client, auth_headers, container
    ):
        container.resolve(IMManager).adapters = {ADAPTER_ID: _PlainAdapter()}

        response = test_client.post(ENDPOINT, headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        # Telegram / WeCom 没有扫码这回事。回一个「不支持」比回 404 好：
        # 404 读起来像「这个适配器不存在」。
        assert body["supported"] is False
        assert body["qr_login"] is None

    def test_unknown_adapter_is_404(self, test_client, auth_headers, container):
        container.resolve(IMManager).adapters = {}

        response = test_client.post(
            "/backend-api/api/im/adapters/nope/qr-login", headers=auth_headers
        )

        assert response.status_code == 404

    def test_read_failure_does_not_500(self, test_client, auth_headers, container):
        class _Exploding:
            def _read_qr_login_snapshot(self):
                raise OSError("volume went read-only")

        container.resolve(IMManager).adapters = {ADAPTER_ID: _Exploding()}

        response = test_client.post(ENDPOINT, headers=auth_headers)

        # 观测不能成为新的失败点：读日志失败时说读不到，而不是把整个面板打挂。
        assert response.status_code == 200
        assert response.json()["qr_login"] is None


class TestAuthorizationIsRequired:
    def test_unauthenticated_request_is_rejected(self, test_client, container):
        container.resolve(IMManager).adapters = {ADAPTER_ID: _QRAdapter([_snapshot()])}

        response = test_client.post(ENDPOINT)

        assert response.status_code in {401, 403}
