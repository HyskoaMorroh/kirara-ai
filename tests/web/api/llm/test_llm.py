from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from kirara_ai.config.config_loader import ConfigLoader
from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig, ModelConfig, WebConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.adapter import LLMBackendAdapter, LLMChatProtocol
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message, Usage
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.llm_registry import LLMAbility, LLMBackendRegistry
from kirara_ai.web.app import WebServer
from tests.utils.auth_test_utils import auth_headers, setup_auth_service  # noqa

# ==================== 常量区 ====================
TEST_PASSWORD = "test-password"
TEST_SECRET_KEY = "test-secret-key"
TEST_BACKEND_NAME = "test-backend"
TEST_ADAPTER_TYPE = "test-adapter"
RESILIENCE_SETTINGS = {
    "auto_detect_interval_days": 9,
    "priority": 7,
    "participate_in_failover": False,
    "max_retries": 2,
    "retry_backoff_seconds": 0.25,
    "retry_backoff_max_seconds": 3.5,
    "request_timeout_seconds": 42.0,
    "circuit_failure_threshold": 4,
    "circuit_error_rate_threshold": 0.75,
    "circuit_min_requests": 8,
    "circuit_recovery_timeout_seconds": 17.0,
}


def assert_resilience_settings(backend, expected=RESILIENCE_SETTINGS):
    for field, value in expected.items():
        assert backend[field] == value


# ==================== 测试用适配器 ====================
class TestConfig(BaseModel):
    """测试用配置"""

    __test__ = False
    api_key: str = "test-key"
    model: str = "test-model"


class TestAdapter(LLMBackendAdapter, LLMChatProtocol):
    """测试用LLM适配器"""

    __test__ = False

    def __init__(self, config: TestConfig):
        self.config = config

    def chat(self, req: LLMChatRequest) -> LLMChatResponse:
        return LLMChatResponse(
            message=Message(
                content=[
                    LLMChatTextContent(text="Test response")
                ],
                role="assistant"
            ),
            model=self.config.model,
            usage=Usage(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30
            ),
        )

    async def auto_detect_models(self):
        """旧适配器仍可能返回字符串 ID；接口必须兼容并规范化它。"""
        return [
            "latest-text",
            ModelConfig(id="latest-vision", type="llm", ability=0),
            "latest-text",
        ]

# ==================== Fixtures ====================
@pytest.fixture(scope="session")
def app():
    """创建测试应用实例"""
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())

    # 配置mock
    config = GlobalConfig()
    config.web = WebConfig(
        secret_key=TEST_SECRET_KEY, password_file="test_password.hash"
    )
    config.llms.api_backends = [
        LLMBackendConfig(
            name=TEST_BACKEND_NAME,
            adapter=TEST_ADAPTER_TYPE,
            config={"api_key": "test-key", "model": "test-model"},
            enable=True,
            models=["test-model"],
            **RESILIENCE_SETTINGS,
        )
    ]
    container.register(GlobalConfig, config)

    # 设置认证服务
    setup_auth_service(container)

    # 注册LLM组件
    registry = LLMBackendRegistry()
    registry.register(TEST_ADAPTER_TYPE, TestAdapter, TestConfig, LLMAbility.TextChat)
    container.register(LLMBackendRegistry, registry)

    manager = LLMManager(container)
    container.register(LLMManager, manager)

    manager.load_config()

    web_server = WebServer(container)
    container.register(WebServer, web_server)
    return web_server.app


@pytest.fixture
def test_client(app):
    """创建测试客户端"""
    return TestClient(app)


# ==================== 测试用例 ====================
class TestLLMBackend:
    @pytest.mark.asyncio
    async def test_get_adapter_types(self, test_client, auth_headers):
        """测试获取适配器类型列表"""
        response = test_client.get(
            "/backend-api/api/llm/types", headers=auth_headers
        )

        data = response.json()
        assert "types" in data
        assert TEST_ADAPTER_TYPE in data.get("types")

    @pytest.mark.asyncio
    async def test_list_backends(self, test_client, auth_headers):
        """测试获取后端列表"""
        response = test_client.get(
            "/backend-api/api/llm/backends", headers=auth_headers
        )

        data = response.json()
        assert "data" in data
        assert "backends" in data.get("data")
        backends = data.get("data").get("backends")
        assert len(backends) == 1
        assert backends[0].get("name") == TEST_BACKEND_NAME
        assert backends[0].get("adapter") == TEST_ADAPTER_TYPE
        assert_resilience_settings(backends[0])

    @pytest.mark.asyncio
    async def test_get_backend(self, test_client, auth_headers):
        """测试获取指定后端"""
        response = test_client.get(
            f"/backend-api/api/llm/backends/{TEST_BACKEND_NAME}", headers=auth_headers
        )

        data = response.json()
        assert "data" in data
        backend = data.get("data")
        assert backend.get("name") == TEST_BACKEND_NAME
        assert backend.get("adapter") == TEST_ADAPTER_TYPE
        assert_resilience_settings(backend)

    @pytest.mark.asyncio
    async def test_resilience_status_requires_authentication(self, test_client):
        response = test_client.get("/backend-api/api/llm/resilience/status")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_resilience_status_is_authenticated_and_sanitized(
        self, test_client, auth_headers
    ):
        response = test_client.get(
            "/backend-api/api/llm/resilience/status", headers=auth_headers
        )

        assert response.status_code == 200
        providers = response.json()["data"]
        assert providers[0]["provider"] == TEST_BACKEND_NAME
        assert providers[0]["model"] == "test-model"
        assert providers[0]["priority"] == RESILIENCE_SETTINGS["priority"]

        serialized = response.text.lower()
        assert "test-key" not in serialized
        assert "api_key" not in serialized
        assert "authorization" not in serialized
        assert "cookie" not in serialized
        assert "error_summary" not in serialized

    @pytest.mark.asyncio
    async def test_auto_detect_models_normalizes_legacy_string_ids(self, test_client, auth_headers):
        """手动检测与定时检测都只更新模型目录，且兼容旧适配器返回的字符串。"""
        response = test_client.get(
            f"/backend-api/api/llm/backends/{TEST_BACKEND_NAME}/auto-detect-models",
            headers=auth_headers,
        )

        assert response.status_code == 200
        models = response.json()["models"]
        assert [model["id"] for model in models] == ["latest-text", "latest-vision"]
        assert models[0]["type"] == "llm"
        assert models[1]["ability"] > 0

    @pytest.mark.asyncio
    async def test_create_backend(self, test_client, auth_headers):
        """测试创建新后端"""
        new_backend = LLMBackendConfig(
            name="new-backend",
            adapter=TEST_ADAPTER_TYPE,
            config={"api_key": "new-key", "model": "new-model"},
            enable=True,
            models=["new-model"],
            **RESILIENCE_SETTINGS,
        )

        # Mock 配置文件保存
        with patch(
            "kirara_ai.config.config_loader.ConfigLoader.save_config_with_backup"
        ) as mock_save:
            response = test_client.post(
                "/backend-api/api/llm/backends",
                headers=auth_headers,
                json=new_backend.model_dump(),
            )

            data = response.json()
            assert "data" in data
            backend = data.get("data")
            assert backend.get("name") == "new-backend"
            assert backend.get("adapter") == TEST_ADAPTER_TYPE
            assert_resilience_settings(backend)

            # 验证配置保存
            mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_backend(self, test_client, auth_headers):
        """测试更新后端"""
        updated_config = LLMBackendConfig(
            name=TEST_BACKEND_NAME,
            adapter=TEST_ADAPTER_TYPE,
            config={"api_key": "updated-key", "model": "updated-model"},
            enable=True,
            models=["updated-model"],
            **RESILIENCE_SETTINGS,
        )

        # Mock 配置文件保存
        ConfigLoader.save_config_with_backup = MagicMock()
        response = test_client.put(
            f"/backend-api/api/llm/backends/{TEST_BACKEND_NAME}",
            headers=auth_headers,
            json=updated_config.model_dump(),
        )

        data = response.json()
        assert not data.get("error")
        assert "data" in data
        backend = data.get("data")
        assert backend.get("name") == TEST_BACKEND_NAME
        assert backend.get("config").get("api_key") == "updated-key"
        assert_resilience_settings(backend)

        # 验证配置保存
        ConfigLoader.save_config_with_backup.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_backend_restores_previous_state_when_loading_fails(
        self, test_client, auth_headers
    ):
        """更新后的后端无法加载时，配置和运行时后端都必须回滚。"""
        original_response = test_client.get(
            f"/backend-api/api/llm/backends/{TEST_BACKEND_NAME}",
            headers=auth_headers,
        )
        original_backend = original_response.json()["data"]
        updated_config = LLMBackendConfig(
            name=TEST_BACKEND_NAME,
            adapter=TEST_ADAPTER_TYPE,
            config={"api_key": "failing-key", "model": "failing-model"},
            enable=True,
            models=["failing-model"],
        )
        original_load_backend = LLMManager.load_backend
        load_attempts = 0

        def fail_once_then_restore(manager, backend_name):
            nonlocal load_attempts
            load_attempts += 1
            if load_attempts == 1:
                raise RuntimeError("updated backend cannot be loaded")
            return original_load_backend(manager, backend_name)

        with (
            patch.object(LLMManager, "load_backend", new=fail_once_then_restore),
            patch.object(ConfigLoader, "save_config_with_backup"),
        ):
            response = test_client.put(
                f"/backend-api/api/llm/backends/{TEST_BACKEND_NAME}",
                headers=auth_headers,
                json=updated_config.model_dump(),
            )

        assert response.status_code == 500
        restored_response = test_client.get(
            f"/backend-api/api/llm/backends/{TEST_BACKEND_NAME}",
            headers=auth_headers,
        )
        restored_backend = restored_response.json()["data"]
        assert restored_backend["config"] == original_backend["config"]
        assert load_attempts == 2

    @pytest.mark.asyncio
    async def test_delete_backend(self, test_client, auth_headers):
        """测试删除后端"""
        ConfigLoader.save_config_with_backup = MagicMock()
        response = test_client.delete(
            f"/backend-api/api/llm/backends/{TEST_BACKEND_NAME}", headers=auth_headers
        )

        data = response.json()
        assert not data.get("error")
        assert "data" in data
        backend = data.get("data")
        assert backend.get("name") == TEST_BACKEND_NAME
        assert_resilience_settings(backend)
        ConfigLoader.save_config_with_backup.assert_called_once()

        # 验证后端已被删除
        response = test_client.get(
            f"/backend-api/api/llm/backends/{TEST_BACKEND_NAME}", headers=auth_headers
        )
        data = response.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_get_adapter_config_schema(self, test_client, auth_headers):
        """测试获取适配器配置模式"""
        response = test_client.get(
            f"/backend-api/api/llm/types/{TEST_ADAPTER_TYPE}/config-schema",
            headers=auth_headers,
        )

        data = response.json()
        assert "configSchema" in data
        schema = data.get("configSchema")
        assert schema.get("title") == "TestConfig"
        assert schema.get("type") == "object"
        assert "properties" in schema

        properties = schema.get("properties")
        assert "api_key" in properties
        assert properties["api_key"].get("title") == "Api Key"
        assert properties["api_key"].get("type") == "string"

        assert "model" in properties
        assert properties["model"].get("title") == "Model"
        assert properties["model"].get("type") == "string"
        assert properties["model"].get("default") == "test-model"

    @pytest.mark.asyncio
    async def test_get_adapter_config_schema_not_found(self, test_client, auth_headers):
        """测试获取不存在的适配器配置模式"""
        response = test_client.get(
            "/backend-api/api/llm/types/not-exist/config-schema", headers=auth_headers
        )
        assert response.status_code == 404
