import asyncio
from copy import deepcopy
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from kirara_ai.config.config_loader import ConfigLoader
from kirara_ai.config.global_config import GlobalConfig, IMConfig, WebConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.im.adapter import AdapterHealthSnapshot, IMAdapter
from kirara_ai.im.im_registry import IMRegistry
from kirara_ai.im.manager import IMManager
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.web.api.im.models import IMAdapterConfig
from kirara_ai.web.app import WebServer
from tests.utils.auth_test_utils import auth_headers, setup_auth_service  # noqa

# ==================== 常量区 ====================
TEST_PASSWORD = "test-password"
TEST_SECRET_KEY = "test-secret-key"
TEST_ADAPTER_ID = "dummy-bot-1234"
TEST_ADAPTER_NOT_RUNNING_ID = "dummy-bot-2234"
TEST_ADAPTER_TYPE = "dummy"
TEST_ADAPTER_CONFIG = {"token": "test-token", "name": "Test Bot"}
REDACTED_ADAPTER_CONFIG = {"token": "", "name": "Test Bot"}
LEGACY_HEALTH_FIELDS = {
    "status",
    "connected_account_count",
    "last_heartbeat_age_seconds",
}


# ==================== 测试用 Adapter ====================
class DummyConfig(BaseModel):
    """Dummy 配置文件模型"""

    token: str = Field(description="Dummy Bot Token")
    name: str = Field(description="Bot Name")


class DummyAdapter(IMAdapter):
    """
    用于测试的 Dummy Adapter，实现基本的消息收发功能
    """

    def __init__(self, config: DummyConfig):
        self.config = config
        self.is_running = False
        self.stop_calls = 0
        self.messages = []  # 存储发送的消息
        self.editing_states = {}  # 存储编辑状态

    def convert_to_message(self, raw_message: Any) -> IMMessage:
        return IMMessage(
            sender=ChatSender.from_c2c_chat(
                user_id=raw_message.get("user_id", "default_user"),
                display_name=raw_message.get("display_name", "Default User"),
            ),
            message_elements=[TextMessage(text=raw_message.get("text", ""))],
        )

    async def send_message(self, message: IMMessage, recipient: ChatSender):
        """发送消息"""
        self.messages.append((message, recipient))

    async def start(self):
        """启动 adapter"""
        self.is_running = True

    async def stop(self):
        """停止 adapter"""
        self.stop_calls += 1
        self.is_running = False

    def get_health_snapshot(self) -> AdapterHealthSnapshot:
        return AdapterHealthSnapshot(
            status="connected" if self.is_running else "disconnected",
            connected_account_count=1 if self.is_running else 0,
            last_heartbeat_age_seconds=0.25 if self.is_running else None,
        )


# ==================== Fixtures ====================
@pytest.fixture(scope="session")
def app():
    """创建测试应用实例"""
    container = DependencyContainer()

    loop = asyncio.new_event_loop()
    container.register(asyncio.AbstractEventLoop, loop)
    # 配置
    config = GlobalConfig()

    config.web = WebConfig(
        secret_key=TEST_SECRET_KEY, password_file="test_password.hash"
    )
    config.ims = [
        IMConfig(
            name=TEST_ADAPTER_ID,
            enable=True,
            adapter=TEST_ADAPTER_TYPE,
            config=TEST_ADAPTER_CONFIG,
        ),
        IMConfig(
            name=TEST_ADAPTER_NOT_RUNNING_ID,
            enable=False,
            adapter=TEST_ADAPTER_TYPE,
            config=TEST_ADAPTER_CONFIG,
        ),
    ]
    container.register(GlobalConfig, config)
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())
    # 创建并注册 IMRegistry
    registry = IMRegistry()
    try:
        registry.register(TEST_ADAPTER_TYPE, DummyAdapter, DummyConfig)
    except Exception as e:
        print(e)
    container.register(IMRegistry, registry)

    # 创建并注册 IMManager
    manager = IMManager(container)
    container.register(IMManager, manager)

    manager.start_adapters(loop=loop)
    web_server = WebServer(container)
    container.register(WebServer, web_server)
    web_server.app.state.container = container
    
    # 设置认证服务
    setup_auth_service(container)
    return web_server.app


@pytest.fixture(scope="session")
def test_client(app):
    """创建测试客户端"""
    return TestClient(app)


# ==================== 测试用例 ====================
class TestIMAdapter:
    @pytest.mark.asyncio
    async def test_get_adapter_types(self, test_client, auth_headers):
        """测试获取适配器类型列表"""
        response = test_client.get(
            "/backend-api/api/im/types", headers=auth_headers
        )

        data = response.json()
        assert "types" in data
        assert TEST_ADAPTER_TYPE in data.get("types")

    @pytest.mark.asyncio
    async def test_list_adapters(self, test_client, auth_headers):
        """测试获取适配器列表"""
        response = test_client.get(
            "/backend-api/api/im/adapters", headers=auth_headers
        )

        data = response.json()
        assert "adapters" in data
        adapters = data.get("adapters")
        assert len(adapters) == 2  # 应该有两个适配器
        adapter = next(a for a in adapters if a.get("name") == TEST_ADAPTER_ID)
        assert adapter.get("adapter") == TEST_ADAPTER_TYPE
        assert adapter.get("is_running") is True
        assert adapter.get("health") == {
            "status": "connected",
            "connected_account_count": 1,
            "last_heartbeat_age_seconds": 0.25,
        }
        assert set(adapter["health"]) == LEGACY_HEALTH_FIELDS
        assert adapter.get("config") == REDACTED_ADAPTER_CONFIG
        assert "test-token" not in response.text

    @pytest.mark.asyncio
    async def test_get_adapter(self, test_client, auth_headers):
        """测试获取特定适配器"""
        response = test_client.get(
            f"/backend-api/api/im/adapters/{TEST_ADAPTER_ID}", headers=auth_headers
        )

        data = response.json()
        assert "adapter" in data
        adapter = data.get("adapter")
        assert adapter.get("name") == TEST_ADAPTER_ID
        assert adapter.get("adapter") == TEST_ADAPTER_TYPE
        assert adapter.get("health", {}).get("status") == "connected"
        assert set(adapter["health"]) == LEGACY_HEALTH_FIELDS
        assert adapter.get("config") == REDACTED_ADAPTER_CONFIG
        assert "test-token" not in response.text

    @pytest.mark.asyncio
    async def test_create_adapter(self, test_client, auth_headers):
        """测试创建适配器"""
        adapter_data = IMAdapterConfig(
            name="new-adapter", adapter=TEST_ADAPTER_TYPE, config=TEST_ADAPTER_CONFIG
        )

        # Mock 配置文件保存
        ConfigLoader.save_config_with_backup = MagicMock()
        response = test_client.post(
            "/backend-api/api/im/adapters",
            headers=auth_headers,
            json=adapter_data.model_dump(),
        )

        data = response.json()
        assert "adapter" in data
        adapter = data.get("adapter")
        assert adapter.get("name") == "new-adapter"
        assert adapter.get("adapter") == TEST_ADAPTER_TYPE
        assert set(adapter["health"]) == LEGACY_HEALTH_FIELDS
        assert adapter.get("config") == REDACTED_ADAPTER_CONFIG
        assert "test-token" not in response.text

        # 验证配置保存
        ConfigLoader.save_config_with_backup.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_adapter_reports_running_status(self, app, test_client, auth_headers):
        """启用创建的适配器时，响应必须反映真实运行状态。"""
        manager = app.state.container.resolve(IMManager)
        adapter_name = "running-adapter"
        if manager.has_adapter(adapter_name):
            manager.delete_adapter(adapter_name)

        ConfigLoader.save_config_with_backup = MagicMock()
        response = test_client.post(
            "/backend-api/api/im/adapters",
            headers=auth_headers,
            json=IMAdapterConfig(
                name=adapter_name,
                enable=True,
                adapter=TEST_ADAPTER_TYPE,
                config=TEST_ADAPTER_CONFIG,
            ).model_dump(),
        )

        assert response.status_code == 200
        assert response.json()["adapter"]["is_running"] is True

        manager.delete_adapter(adapter_name)

    @pytest.mark.asyncio
    async def test_update_adapter_rolls_back_when_replacement_start_fails(
        self, app, test_client, auth_headers, monkeypatch
    ):
        """替换适配器启动失败时，旧配置和旧运行态必须完整恢复。"""
        config = app.state.container.resolve(GlobalConfig)
        manager = app.state.container.resolve(IMManager)
        old_configs = deepcopy(config.ims)
        old_adapters = dict(manager.get_adapters())
        old_running = {
            name: getattr(adapter, "is_running", False)
            for name, adapter in old_adapters.items()
        }
        replacement_name = "replacement-start-failure"
        replacement_adapters = []

        original_create_adapter = manager.create_adapter

        def capture_create_adapter(name, adapter_class, adapter_config):
            adapter = original_create_adapter(name, adapter_class, adapter_config)
            if name.startswith("__im_update_"):
                replacement_adapters.append(adapter)
            return adapter

        monkeypatch.setattr(manager, "create_adapter", capture_create_adapter)

        def fail_replacement_start(adapter_id, loop):
            adapter = manager.get_adapter(adapter_id)
            if getattr(getattr(adapter, "config", None), "name", None) == "Replacement":
                future = loop.create_future()
                future.set_exception(RuntimeError("replacement start failed"))
                return future
            return original_start_adapter(adapter_id, loop)

        original_start_adapter = manager.start_adapter
        monkeypatch.setattr(manager, "start_adapter", fail_replacement_start)
        ConfigLoader.save_config_with_backup = MagicMock()

        try:
            response = test_client.put(
                f"/backend-api/api/im/adapters/{TEST_ADAPTER_ID}",
                headers=auth_headers,
                json=IMAdapterConfig(
                    name=replacement_name,
                    enable=True,
                    adapter=TEST_ADAPTER_TYPE,
                    config={"token": "replacement", "name": "Replacement"},
                ).model_dump(),
            )

            assert response.status_code == 500
            assert manager.has_adapter(TEST_ADAPTER_ID)
            assert not manager.has_adapter(replacement_name)
            assert config.ims == old_configs
            assert {
                name: manager.is_adapter_running(name)
                for name in old_adapters
            } == old_running
        finally:
            manager.adapters.clear()
            manager.adapters.update(old_adapters)
            config.ims = old_configs
            for name, adapter in old_adapters.items():
                adapter.is_running = old_running[name]

    @pytest.mark.asyncio
    async def test_update_adapter_rolls_back_when_config_save_fails(
        self, app, test_client, auth_headers, monkeypatch
    ):
        """替换后的配置落盘失败时，旧配置和旧运行态必须完整恢复。"""
        config = app.state.container.resolve(GlobalConfig)
        manager = app.state.container.resolve(IMManager)
        old_configs = deepcopy(config.ims)
        old_adapters = dict(manager.get_adapters())
        old_running = {
            name: getattr(adapter, "is_running", False)
            for name, adapter in old_adapters.items()
        }
        replacement_adapters = []

        original_create_adapter = manager.create_adapter

        def capture_create_adapter(name, adapter_class, adapter_config):
            adapter = original_create_adapter(name, adapter_class, adapter_config)
            if name.startswith("__im_update_"):
                replacement_adapters.append(adapter)
            return adapter

        monkeypatch.setattr(manager, "create_adapter", capture_create_adapter)

        def fail_save(*_args, **_kwargs):
            raise OSError("config disk unavailable")

        monkeypatch.setattr(ConfigLoader, "save_config_with_backup", fail_save)

        try:
            response = test_client.put(
                f"/backend-api/api/im/adapters/{TEST_ADAPTER_ID}",
                headers=auth_headers,
                json=IMAdapterConfig(
                    name="replacement-save-failure",
                    enable=True,
                    adapter=TEST_ADAPTER_TYPE,
                    config={"token": "replacement", "name": "Replacement"},
                ).model_dump(),
            )

            assert response.status_code == 500
            assert len(replacement_adapters) == 1
            assert replacement_adapters[0].stop_calls == 1
            assert manager.has_adapter(TEST_ADAPTER_ID)
            assert not manager.has_adapter("replacement-save-failure")
            assert config.ims == old_configs
            assert {
                name: manager.is_adapter_running(name)
                for name in old_adapters
            } == old_running
        finally:
            manager.adapters.clear()
            manager.adapters.update(old_adapters)
            config.ims = old_configs
            for name, adapter in old_adapters.items():
                adapter.is_running = old_running[name]

    @pytest.mark.asyncio
    async def test_update_adapter(self, app, test_client, auth_headers):
        """测试更新适配器"""
        adapter_data = IMAdapterConfig(
            name=TEST_ADAPTER_ID,
            adapter=TEST_ADAPTER_TYPE,
            config={"token": "updated-token", "name": "Updated Bot"},
        )

        # Mock 配置文件保存
        ConfigLoader.save_config_with_backup = MagicMock()
        response = test_client.put(
            f"/backend-api/api/im/adapters/{TEST_ADAPTER_ID}",
            headers=auth_headers,
            json=adapter_data.model_dump(),
        )

        data = response.json()
        assert "adapter" in data
        adapter = data.get("adapter")
        assert adapter.get("name") == TEST_ADAPTER_ID
        assert adapter.get("adapter") == TEST_ADAPTER_TYPE
        assert adapter.get("config").get("token") == ""
        assert adapter.get("config").get("name") == "Updated Bot"
        assert set(adapter["health"]) == LEGACY_HEALTH_FIELDS
        assert "updated-token" not in response.text

        manager = app.state.container.resolve(IMManager)
        assert manager.get_adapter_config(TEST_ADAPTER_ID).config["token"] == "updated-token"

        # 验证配置保存
        ConfigLoader.save_config_with_backup.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_adapter_blank_secret_keeps_existing_value(
        self, app, test_client, auth_headers
    ):
        """编辑页回传空白密钥时，不覆盖服务器已经保存的值。"""
        manager = app.state.container.resolve(IMManager)
        existing_token = manager.get_adapter_config(TEST_ADAPTER_ID).config["token"]
        adapter_data = IMAdapterConfig(
            name=TEST_ADAPTER_ID,
            adapter=TEST_ADAPTER_TYPE,
            config={"token": "", "name": "Renamed Bot"},
        )

        ConfigLoader.save_config_with_backup = MagicMock()
        response = test_client.put(
            f"/backend-api/api/im/adapters/{TEST_ADAPTER_ID}",
            headers=auth_headers,
            json=adapter_data.model_dump(),
        )

        assert response.status_code == 200
        assert response.json()["adapter"]["config"] == {
            "token": "",
            "name": "Renamed Bot",
        }
        assert manager.get_adapter_config(TEST_ADAPTER_ID).config["token"] == existing_token

    @pytest.mark.asyncio
    async def test_stop_adapter(self, test_client, auth_headers):
        """测试停止适配器"""
        response = test_client.post(
            f"/backend-api/api/im/adapters/{TEST_ADAPTER_ID}/stop", headers=auth_headers
        )
        data = response.json()
        assert "message" in data
        assert data.get("message") == "Adapter stopped successfully"

        # 验证适配器状态
        response = test_client.get(
            f"/backend-api/api/im/adapters/{TEST_ADAPTER_ID}", headers=auth_headers
        )
        data = response.json()
        assert "adapter" in data
        assert data.get("adapter").get("is_running") is False

    @pytest.mark.asyncio
    async def test_start_adapter(self, test_client, auth_headers):
        """测试启动适配器"""
        response = test_client.post(
            f"/backend-api/api/im/adapters/{TEST_ADAPTER_ID}/start",
            headers=auth_headers,
        )
        data = response.json()
        assert "message" in data
        assert data.get("message") == "Adapter started successfully"

        # 验证适配器状态
        response = test_client.get(
            f"/backend-api/api/im/adapters/{TEST_ADAPTER_ID}", headers=auth_headers
        )
        data = response.json()
        assert "adapter" in data
        assert data.get("adapter").get("is_running") is True

    @pytest.mark.asyncio
    async def test_delete_adapter(self, test_client, auth_headers):
        """测试删除适配器"""
        # 先启动适配器
        test_client.post(
            f"/backend-api/api/im/adapters/{TEST_ADAPTER_ID}/start",
            headers=auth_headers,
        )

        # Mock 配置文件保存
        ConfigLoader.save_config_with_backup = MagicMock()
        response = test_client.delete(
            f"/backend-api/api/im/adapters/{TEST_ADAPTER_ID}", headers=auth_headers
        )

        data = response.json()
        assert "message" in data
        assert data.get("message") == "Adapter deleted successfully"

        # 验证配置保存
        ConfigLoader.save_config_with_backup.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_adapter_config_schema(self, test_client, auth_headers):
        """测试获取适配器配置模式"""
        response = test_client.get(
            f"/backend-api/api/im/types/{TEST_ADAPTER_TYPE}/config-schema",
            headers=auth_headers,
        )

        data = response.json()
        assert "configSchema" in data
        schema = data.get("configSchema")
        assert schema.get("title") == "DummyConfig"
        assert schema.get("type") == "object"
        assert "properties" in schema

        properties = schema.get("properties")
        assert "token" in properties
        assert properties["token"].get("title") == "Token"
        assert properties["token"].get("type") == "string"
        assert properties["token"].get("description") == "Dummy Bot Token"

        assert "name" in properties
        assert properties["name"].get("title") == "Name"
        assert properties["name"].get("type") == "string"
        assert properties["name"].get("description") == "Bot Name"

    @pytest.mark.asyncio
    async def test_get_adapter_config_schema_not_found(self, test_client, auth_headers):
        """测试获取不存在的适配器配置模式"""
        response = test_client.get(
            "/backend-api/api/im/types/not-exist/config-schema", headers=auth_headers
        )
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
