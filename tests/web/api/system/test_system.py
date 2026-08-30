from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from kirara_ai.config.global_config import GlobalConfig, WebConfig
from kirara_ai.im.manager import IMManager
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.plugin_manager.plugin_loader import PluginLoader
from kirara_ai.web.app import WebServer
from kirara_ai.web.api.system.utils import ArtifactDigest
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from tests.utils.auth_test_utils import auth_headers, setup_auth_service  # noqa

# ==================== 常量区 ====================
TEST_PASSWORD = "test-password"
TEST_SECRET_KEY = "test-secret-key"


# ==================== Fixtures ====================
@pytest.fixture
def app():
    """创建测试应用实例"""
    container = DependencyContainer()

    # 配置mock
    config = GlobalConfig()
    config.web = WebConfig(
        secret_key=TEST_SECRET_KEY, password_file="test_password.hash"
    )
    container.register(GlobalConfig, config)

    # 设置认证服务
    setup_auth_service(container)

    # Mock其他依赖
    im_manager = MagicMock(spec=IMManager)
    im_manager.adapters = {
        "adapter1": MagicMock(is_running=True),
        "adapter2": MagicMock(is_running=False),
    }
    container.register(IMManager, im_manager)

    llm_manager = MagicMock(spec=LLMManager)
    llm_manager.active_backends = {"backend1": [], "backend2": []}
    container.register(LLMManager, llm_manager)

    plugin_loader = MagicMock(spec=PluginLoader)
    plugin_loader.plugins = [MagicMock(), MagicMock(), MagicMock()]
    container.register(PluginLoader, plugin_loader)

    workflow_registry = MagicMock(spec=WorkflowRegistry)
    workflow_registry.snapshot_builders.return_value = (
        ("workflow1", MagicMock()),
        ("workflow2", MagicMock()),
    )
    container.register(WorkflowRegistry, workflow_registry)

    web_server = WebServer(container)
    container.register(WebServer, web_server)
    return web_server.app


@pytest.fixture
def test_client(app):
    """创建测试客户端"""
    return TestClient(app)


# ==================== 测试用例 ====================
class TestSystemStatus:
    @pytest.mark.asyncio
    async def test_get_system_status(self, test_client, auth_headers):
        """测试获取系统状态"""
        # Mock psutil.Process
        mock_process = MagicMock()
        mock_process.memory_full_info.return_value = MagicMock(
            uss=1024 * 1024 * 100  # 100MB
        )
        mock_process.cpu_percent.return_value = 1.2
        
        # Mock psutil.virtual_memory
        mock_virtual_memory = MagicMock()
        mock_virtual_memory.total = 1024 * 1024 * 8192  # 8GB
        mock_virtual_memory.available = 1024 * 1024 * 4096  # 4GB
        mock_virtual_memory.used = 1024 * 1024 * 4096  # 4GB

        with patch(
            "kirara_ai.web.api.system.utils.psutil.Process", return_value=mock_process
        ), patch(
            "kirara_ai.web.api.system.utils.psutil.virtual_memory", return_value=mock_virtual_memory
        ), patch(
            "kirara_ai.web.api.system.utils.psutil.cpu_percent", return_value=1.2
        ):
            response = test_client.get(
                "/backend-api/api/system/status", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()

            assert "status" in data
            status = data["status"]

            # 验证基本字段
            assert "version" in status
            assert "uptime" in status
            assert status["active_adapters"] == 1  # 只有一个运行中的适配器
            assert status["active_backends"] == 2  # 两个后端
            assert status["loaded_plugins"] == 3  # 三个插件
            assert status["workflow_count"] == 2  # 两个工作流

            # 验证资源使用情况
            assert "memory_usage" in status
            assert "cpu_usage" in status
            assert status["memory_usage"]["percent"] == 0.5  # used/total
            assert status["memory_usage"]["total"] == 8192  # 8GB
            assert status["memory_usage"]["free"] == 4096  # 4GB
            assert status["memory_usage"]["used"] == 100  # 100MB (process.memory_full_info().uss)
            assert status["cpu_usage"] == 1.2

    @pytest.mark.asyncio
    async def test_get_system_status_unauthorized(self, test_client):
        """测试未认证时获取系统状态"""
        response = test_client.get("/backend-api/api/system/status")

        assert response.status_code == 401
        data = response.json()
        assert "error" in data


    @pytest.mark.asyncio
    async def test_check_update(self, test_client, auth_headers):
        """测试检查更新"""
        response = test_client.get("/backend-api/api/system/check-update", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["current_backend_version"] != "0.0.0"
        assert data["latest_backend_version"] != "0.0.0"
        assert data["backend_update_available"] == False
        assert data["latest_webui_version"] != "0.0.0"
        assert data["webui_download_url"] != ""

    def test_check_update_never_displays_registry_downgrades(
        self, test_client, auth_headers
    ):
        with patch(
            "kirara_ai.web.api.system.routes.get_installed_version",
            return_value="3.3.0b8",
        ), patch(
            "kirara_ai.web.api.system.routes.get_installed_webui_version",
            return_value="3.3.0-b8",
        ), patch(
            "kirara_ai.web.api.system.routes.get_latest_pypi_version",
            AsyncMock(
                return_value=(
                    "3.2.0",
                    "https://files.pythonhosted.org/kirara_ai-3.2.0.whl",
                )
            ),
        ), patch(
            "kirara_ai.web.api.system.routes.get_latest_npm_version",
            AsyncMock(
                return_value=(
                    "0.1.1-beta.3",
                    "https://registry.npmjs.org/kirara-ai-webui/-/kirara-ai-webui-0.1.1-beta.3.tgz",
                )
            ),
        ):
            response = test_client.get(
                "/backend-api/api/system/check-update", headers=auth_headers
            )

        assert response.status_code == 200
        data = response.json()
        assert data["latest_backend_version"] == data["current_backend_version"]
        assert data["backend_update_available"] is False
        assert data["backend_download_url"] is None
        assert data["latest_webui_version"] == "3.3.0-b8"
        assert data["webui_download_url"] is None

    @pytest.mark.parametrize("invalid_version", ["", "unknown", "not-a-version", None])
    def test_check_update_treats_invalid_registry_versions_as_unavailable(
        self, test_client, auth_headers, invalid_version
    ):
        with patch(
            "kirara_ai.web.api.system.routes.get_latest_pypi_version",
            AsyncMock(return_value=(invalid_version, "https://invalid.example/file.whl")),
        ), patch(
            "kirara_ai.web.api.system.routes.get_latest_npm_version",
            AsyncMock(return_value=(invalid_version, "https://invalid.example/file.tgz")),
        ):
            response = test_client.get(
                "/backend-api/api/system/check-update", headers=auth_headers
            )

        assert response.status_code == 200
        data = response.json()
        assert data["backend_update_available"] is False
        assert data["backend_download_url"] is None
        assert data["webui_download_url"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("invalid_version", ["", "unknown", "not-a-version", None])
    async def test_startup_update_check_ignores_invalid_registry_versions(
        self, invalid_version, caplog
    ):
        from kirara_ai import entry

        config = GlobalConfig()
        with patch(
            "kirara_ai.entry.get_installed_version", return_value="3.3.0b8"
        ), patch(
            "kirara_ai.entry.get_latest_pypi_version",
            AsyncMock(return_value=(invalid_version, "")),
        ):
            await entry.check_update(config)

        assert "available" not in caplog.text.lower()


class TestSystemUpdate:
    def test_backend_update_ignores_client_download_url(
        self, test_client, auth_headers
    ):
        trusted_url = "https://files.pythonhosted.org/kirara_ai-3.3.0b8.whl"
        download = AsyncMock(return_value=("trusted.whl", "sha256"))

        # 安装路径改走带摘要的 `resolve_pypi_release`（需求 16：装之前比对
        # registry 声明的哈希）。这里把校验本身替掉，因为本用例断言的是
        # 「可信 URL 覆盖客户端传入的 URL」，不是校验逻辑——后者有
        # `test_update_integrity.py` 专门覆盖。
        with patch(
            "kirara_ai.web.api.system.routes.get_installed_version",
            return_value="3.3.0b7",
        ), patch(
            "kirara_ai.web.api.system.routes.resolve_pypi_release",
            AsyncMock(return_value=("3.3.0b8", trusted_url, ArtifactDigest(sha256="x" * 64))),
        ) as lookup, patch(
            "kirara_ai.web.api.system.routes.download_file", download
        ), patch(
            "kirara_ai.web.api.system.routes.verify_artifact_digest"
        ) as verify, patch("kirara_ai.web.api.system.routes.subprocess.run") as install:
            response = test_client.post(
                "/backend-api/api/system/update",
                headers=auth_headers,
                json={
                    "update_backend": True,
                    "update_webui": False,
                    "backend_download_url": "https://attacker.invalid/old.whl",
                },
            )

        assert response.status_code == 200
        assert download.await_args.args[0] == trusted_url
        assert "attacker.invalid" not in download.await_args.args[0]
        assert lookup.await_args.args == (
            "kirara-ai",
            "https://pypi.org/simple",
        )
        # 校验必须真的被调用过，且用的是 registry 给的摘要。
        verify.assert_called_once()
        assert verify.call_args.args[1].sha256 == "x" * 64
        install.assert_called_once()

    def test_backend_update_rejects_same_or_older_version_before_download(
        self, test_client, auth_headers
    ):
        download = AsyncMock()

        with patch(
            "kirara_ai.web.api.system.routes.get_installed_version",
            return_value="3.3.0b8",
        ), patch(
            "kirara_ai.web.api.system.routes.get_latest_pypi_version",
            AsyncMock(
                return_value=(
                    "3.3.0b7",
                    "https://files.pythonhosted.org/kirara_ai-3.3.0b7.whl",
                )
            ),
        ), patch(
            "kirara_ai.web.api.system.routes.download_file", download
        ), patch("kirara_ai.web.api.system.routes.subprocess.run") as install:
            response = test_client.post(
                "/backend-api/api/system/update",
                headers=auth_headers,
                json={
                    "update_backend": True,
                    "backend_download_url": "https://attacker.invalid/old.whl",
                },
            )

        assert response.status_code == 409
        assert "newer" in response.json()["message"].lower()
        download.assert_not_awaited()
        install.assert_not_called()

    def test_webui_update_uses_registry_url_and_rejects_downgrade(
        self, test_client, auth_headers
    ):
        trusted_url = "https://registry.npmjs.org/kirara-ai-webui/-/kirara-ai-webui-3.3.0-b7.tgz"
        download = AsyncMock()

        with patch(
            "kirara_ai.web.api.system.routes.get_installed_webui_version",
            return_value="3.3.0-b8",
            create=True,
        ), patch(
            "kirara_ai.web.api.system.routes.resolve_npm_release",
            AsyncMock(return_value=("3.3.0-b7", trusted_url, ArtifactDigest())),
        ) as lookup, patch(
            "kirara_ai.web.api.system.routes.download_file", download
        ):
            response = test_client.post(
                "/backend-api/api/system/update",
                headers=auth_headers,
                json={
                    "update_webui": True,
                    "webui_download_url": "https://attacker.invalid/old.tgz",
                },
            )

        assert response.status_code == 409
        assert "newer" in response.json()["message"].lower()
        lookup.assert_awaited_once_with(
            "kirara-ai-webui",
            "https://registry.npmjs.org",
            dist_tag="beta",
        )
        download.assert_not_awaited()

    def test_update_requires_at_least_one_component(self, test_client, auth_headers):
        response = test_client.post(
            "/backend-api/api/system/update",
            headers=auth_headers,
            json={"update_backend": False, "update_webui": False},
        )

        assert response.status_code == 400
