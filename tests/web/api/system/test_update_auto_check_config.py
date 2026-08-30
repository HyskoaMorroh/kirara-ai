"""「禁用自动升级」必须能从界面配，而不只能改 config.yaml（需求 8）。

`update.disable_auto_check` 有真实消费点：`entry.py::check_update` 打开时
**完全不发起请求**（离线/内网部署既查不到注册表又要等超时）。但通往它的路只有
一条——手改 config.yaml：

- `GET /system/config` 的 `update` 段只返回两个镜像源，不返回这个开关；
- `POST /system/config/update` 只写两个镜像源，收到这个键也会丢掉。

于是它和修好之前的 `hide_ai_attribution` 是同一种缺陷：后端读得到、
前端到不了。需求 8 要的是「在模型管理/设置里完全实现 cc-switch 的供应商编辑」，
不是「在 YAML 里能写」。

另有一处比「填不了」更隐蔽：这个路由用 `data["pypi_registry"]` 直接下标，
只提交开关的请求会 KeyError → 500。补齐时一并按「没提交的键保留原值」处理，
与 `PUT /llm/backends/{name}` 的 `exclude_unset` 语义保持一致。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from kirara_ai.config.global_config import GlobalConfig, WebConfig
from kirara_ai.im.manager import IMManager
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.plugin_manager.plugin_loader import PluginLoader
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from tests.utils.auth_test_utils import auth_headers, setup_auth_service  # noqa

TEST_SECRET_KEY = "test-secret-key"


@pytest.fixture
def container() -> DependencyContainer:
    container = DependencyContainer()
    config = GlobalConfig()
    config.web = WebConfig(
        secret_key=TEST_SECRET_KEY, password_file="test_password.hash"
    )
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


@pytest.fixture
def saved(monkeypatch):
    """拦落盘，返回被保存的 config 引用。

    用 `monkeypatch` 而不是给类属性直接赋 MagicMock：后者不会在用例结束时还原，
    整个目录一起跑时后续用例会拿到一个永远不写盘的 saver。
    """
    from kirara_ai.web.api.system import routes as system_routes

    calls: list[GlobalConfig] = []
    monkeypatch.setattr(
        system_routes.ConfigLoader,
        "save_config_with_backup",
        lambda _path, cfg: calls.append(cfg),
    )
    return calls


class TestDisableAutoCheckIsReachable:
    def test_get_config_reports_the_switch(self, test_client, auth_headers):
        """读不到当前值，界面就只能猜——开关会显示成它猜的那个状态。"""
        response = test_client.get("/backend-api/api/system/config", headers=auth_headers)
        assert response.status_code == 200
        update_section = response.json()["update"]
        assert "disable_auto_check" in update_section, (
            "GET /system/config 不返回 disable_auto_check，界面无法回显真实状态"
        )
        assert update_section["disable_auto_check"] is False

    def test_post_config_can_turn_it_on(
        self, test_client, auth_headers, container, saved
    ):
        response = test_client.post(
            "/backend-api/api/system/config/update",
            headers=auth_headers,
            json={
                "pypi_registry": "https://pypi.org/simple",
                "npm_registry": "https://registry.npmjs.org",
                "disable_auto_check": True,
            },
        )
        assert response.status_code == 200, response.text
        assert container.resolve(GlobalConfig).update.disable_auto_check is True
        assert saved, "改了配置却没落盘，重启后开关自己回到关闭"

    def test_post_config_can_turn_it_off_again(
        self, test_client, auth_headers, container, saved
    ):
        """能开不能关就是个单向阀，用户只能回去改 YAML。"""
        container.resolve(GlobalConfig).update.disable_auto_check = True
        response = test_client.post(
            "/backend-api/api/system/config/update",
            headers=auth_headers,
            json={
                "pypi_registry": "https://pypi.org/simple",
                "npm_registry": "https://registry.npmjs.org",
                "disable_auto_check": False,
            },
        )
        assert response.status_code == 200, response.text
        assert container.resolve(GlobalConfig).update.disable_auto_check is False

    def test_omitted_switch_keeps_its_stored_value(
        self, test_client, auth_headers, container, saved
    ):
        """老前端只发两个镜像源；那不该被当成「用户要求关掉自动检查抑制」。"""
        container.resolve(GlobalConfig).update.disable_auto_check = True
        response = test_client.post(
            "/backend-api/api/system/config/update",
            headers=auth_headers,
            json={
                "pypi_registry": "https://mirrors.aliyun.com/pypi/simple",
                "npm_registry": "https://registry.npmmirror.com",
            },
        )
        assert response.status_code == 200, response.text
        config = container.resolve(GlobalConfig)
        assert config.update.pypi_registry == "https://mirrors.aliyun.com/pypi/simple"
        assert config.update.disable_auto_check is True, (
            "请求里没有 disable_auto_check，它被重置成了默认值 False"
        )

    def test_omitted_registries_keep_their_stored_values(
        self, test_client, auth_headers, container, saved
    ):
        """只想关自动检查的请求不该 500，也不该把镜像源清成默认。"""
        config = container.resolve(GlobalConfig)
        config.update.pypi_registry = "https://pypi.tuna.tsinghua.edu.cn/simple"
        response = test_client.post(
            "/backend-api/api/system/config/update",
            headers=auth_headers,
            json={"disable_auto_check": True},
        )
        assert response.status_code == 200, response.text
        assert config.update.disable_auto_check is True
        assert config.update.pypi_registry == "https://pypi.tuna.tsinghua.edu.cn/simple"

    def test_blank_registry_is_rejected_instead_of_saved(
        self, test_client, auth_headers, container, saved
    ):
        """空镜像源存下去，下次检查更新会拿空 URL 发请求。"""
        config = container.resolve(GlobalConfig)
        original = config.update.pypi_registry
        response = test_client.post(
            "/backend-api/api/system/config/update",
            headers=auth_headers,
            json={"pypi_registry": "   "},
        )
        assert response.status_code == 400, response.text
        assert config.update.pypi_registry == original


class TestAutomaticChecksActuallyStop:
    """开关的承诺是「不自动去外网」，而 WebUI 每次挂载都会自动去一次。

    `StatusBar.vue` 在 `onMounted` 里无条件调 `checkUpdate()` → `GET /system/check-update`
    → PyPI + npm。所以此前 `disable_auto_check` 只挡住了**启动时**那一次探测：
    离线部署每打开一次页面仍然要等两次超时，而这正是这个开关声称要消掉的等待。

    这里定的语义：
    - 开关打开时，`check-update` 默认**不外呼**，返回当前版本 + `checked=False`，
      让界面能说清「没查，不是查到没更新」；
    - `?manual=1` 是用户主动点的，照常外呼——`global_config.py` 里写明了
      「WebUI 的检查更新按钮仍然可用」，那句承诺必须真的成立。
    """

    def test_automatic_check_does_not_call_out_when_disabled(
        self, test_client, auth_headers, container
    ):
        from unittest.mock import AsyncMock, patch

        container.resolve(GlobalConfig).update.disable_auto_check = True
        pypi = AsyncMock(return_value=("9.9.9", "https://example.invalid/x.whl"))
        npm = AsyncMock(return_value=("9.9.9", "https://example.invalid/x.tgz"))
        with patch(
            "kirara_ai.web.api.system.routes.get_latest_pypi_version", pypi
        ), patch("kirara_ai.web.api.system.routes.get_latest_npm_version", npm):
            response = test_client.get(
                "/backend-api/api/system/check-update", headers=auth_headers
            )

        assert response.status_code == 200, response.text
        pypi.assert_not_awaited()
        npm.assert_not_awaited()
        data = response.json()
        assert data["checked"] is False, (
            "必须能区分「没查」与「查了没更新」，否则界面只能谎报其中一种"
        )
        assert data["backend_update_available"] is False
        assert data["backend_download_url"] is None
        assert data["webui_download_url"] is None

    def test_manual_check_still_calls_out_when_disabled(
        self, test_client, auth_headers, container
    ):
        """`global_config.py` 承诺过手动按钮仍可用；不兑现就是文档在说谎。"""
        from unittest.mock import AsyncMock, patch

        container.resolve(GlobalConfig).update.disable_auto_check = True
        pypi = AsyncMock(return_value=("9.9.9", "https://example.invalid/x.whl"))
        npm = AsyncMock(return_value=("9.9.9", "https://example.invalid/x.tgz"))
        with patch(
            "kirara_ai.web.api.system.routes.get_latest_pypi_version", pypi
        ), patch("kirara_ai.web.api.system.routes.get_latest_npm_version", npm):
            response = test_client.get(
                "/backend-api/api/system/check-update?manual=1", headers=auth_headers
            )

        assert response.status_code == 200, response.text
        pypi.assert_awaited()
        assert response.json()["checked"] is True

    def test_enabled_auto_check_is_unchanged(self, test_client, auth_headers, container):
        """默认路径不能因为加了开关就变味。"""
        from unittest.mock import AsyncMock, patch

        assert container.resolve(GlobalConfig).update.disable_auto_check is False
        pypi = AsyncMock(return_value=("9.9.9", "https://example.invalid/x.whl"))
        npm = AsyncMock(return_value=("9.9.9", "https://example.invalid/x.tgz"))
        with patch(
            "kirara_ai.web.api.system.routes.get_latest_pypi_version", pypi
        ), patch("kirara_ai.web.api.system.routes.get_latest_npm_version", npm):
            response = test_client.get(
                "/backend-api/api/system/check-update", headers=auth_headers
            )

        assert response.status_code == 200, response.text
        pypi.assert_awaited()
        assert response.json()["checked"] is True
