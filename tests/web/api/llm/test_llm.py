from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from kirara_ai.config.config_loader import ConfigLoader
from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig, ModelConfig, WebConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentRegistry,
    AgentRuntimeExecutor,
    RuntimeResult,
    RuntimeStatus,
)
from kirara_ai.im.sender import ChatType
from kirara_ai.llm.adapter import LLMBackendAdapter, LLMChatProtocol
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message, Usage
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.llm_registry import LLMAbility, LLMBackendRegistry
from kirara_ai.web.app import WebServer, create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService
from kirara_ai.workflow.core.dispatch.dispatcher import _NO_AGENT_MESSAGE, WorkflowDispatcher
from kirara_ai.workflow.core.dispatch.models.dispatch_rules import CombinedDispatchRule
from kirara_ai.workflow.core.dispatch.registry import DispatchRuleRegistry
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry
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

SENSITIVE_CONFIG = {
    "api_key": "test-key",
    "token": "test-token",
    "Authorization": "Bearer test-authorization",
    "Cookie": "session=test-cookie",
    "password": "test-password-value",
    "client_secret": "test-client-secret",
    "credential": "test-credential",
    "nested": {"refresh_token": "test-refresh-token"},
}


def assert_backend_secrets_redacted(backend, *secret_values: str):
    config = backend["config"]
    assert config["api_key"] == ""
    assert config["token"] == ""
    assert config["Authorization"] == ""
    assert config["Cookie"] == ""
    assert config["password"] == ""
    assert config["client_secret"] == ""
    assert config["credential"] == ""
    assert config["nested"]["refresh_token"] == ""
    serialized = str(backend)
    pending = list(secret_values)
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, str):
            assert value not in serialized


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


class _ChatWorkflowRegistry:
    def get_workflow(self, workflow_id, container):
        return object()


class _ChatRuntime:
    def __init__(self):
        self.calls = []

    async def run(self, context, message, **options):
        self.calls.append((context, message, options))
        return RuntimeResult(
            status=RuntimeStatus.COMPLETED,
            text=f"reply:{message.content}",
            context=context,
            agent_id=options.get("session_agent_id"),
        )


@pytest.fixture
def chat_app():
    """Small real Quart graph for the WebUI-to-dispatcher contract."""
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())
    container.register(AuthService, MockAuthService())
    container.register(GlobalConfig, GlobalConfig())
    container.register(WorkflowRegistry, _ChatWorkflowRegistry())
    dispatch_registry = DispatchRuleRegistry(container)
    dispatch_registry.register(
        CombinedDispatchRule(
            rule_id="webui-fallback",
            name="WebUI fallback",
            workflow_id="chat:normal",
            rule_groups=[],
        )
    )
    container.register(DispatchRuleRegistry, dispatch_registry)
    registry = AgentRegistry()
    registry.register(AgentDefinition(agent_id="webui-agent", model_priority=("model-a",)))
    registry.register(AgentDefinition(agent_id="selected-agent", model_priority=("model-a",)))
    registry.set_default("webui-agent")
    container.register(AgentRegistry, registry)
    runtime = _ChatRuntime()
    container.register(AgentRuntimeExecutor, runtime)
    container.register(WorkflowDispatcher, WorkflowDispatcher(container))
    return create_web_api_app(container), runtime


@pytest.fixture
def chat_app_without_agent(monkeypatch):
    """Real WebUI graph with an empty Agent registry and a fallback workflow."""
    workflow_run = MagicMock(return_value={"workflow": "completed"})

    class _WorkflowExecutor:
        def __init__(self, container):
            self.container = container

        async def run(self):
            return workflow_run()

    monkeypatch.setattr(
        "kirara_ai.workflow.core.dispatch.dispatcher.WorkflowExecutor",
        _WorkflowExecutor,
    )
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())
    container.register(AuthService, MockAuthService())
    container.register(GlobalConfig, GlobalConfig())
    container.register(WorkflowRegistry, _ChatWorkflowRegistry())
    dispatch_registry = DispatchRuleRegistry(container)
    dispatch_registry.register(
        CombinedDispatchRule(
            rule_id="webui-fallback",
            name="WebUI fallback",
            workflow_id="chat:normal",
            rule_groups=[],
        )
    )
    container.register(DispatchRuleRegistry, dispatch_registry)
    container.register(AgentRegistry, AgentRegistry())
    runtime = _ChatRuntime()
    container.register(AgentRuntimeExecutor, runtime)
    container.register(WorkflowDispatcher, WorkflowDispatcher(container))
    return create_web_api_app(container), runtime, workflow_run


@pytest.mark.asyncio
async def test_webui_chat_requires_authentication(chat_app):
    app, _ = chat_app
    client = app.test_client()

    response = await client.post(
        "/api/llm/chat",
        json={"message": "hello", "session_id": "research-1"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_webui_chat_dispatches_private_message_through_agent_runtime(chat_app):
    app, runtime = chat_app
    client = app.test_client()

    response = await client.post(
        "/api/llm/chat",
        headers={"Authorization": "Bearer mock_token"},
        json={
            "message": "summarize the paper",
            "session_id": "research-1",
            "username": "Researcher",
        },
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload == {
        "status": "completed",
        "text": "reply:summarize the paper",
        "agent_id": "webui-agent",
        "session_id": "research-1",
        "session_key": "webui/webui/webui/c2c:research-1/research-1",
        "confirmation_id": None,
    }
    context, message, options = runtime.calls[0]
    assert context.channel_type == "webui"
    assert context.adapter_instance == "webui"
    assert context.account_scope == "webui"
    assert message.sender.chat_type is ChatType.C2C
    assert options["session_agent_id"] == "webui-agent"


@pytest.mark.asyncio
async def test_webui_chat_allows_explicit_agent_and_preserves_group_scope(chat_app):
    app, runtime = chat_app
    client = app.test_client()

    response = await client.post(
        "/api/llm/chat",
        headers={"Authorization": "Bearer mock_token"},
        json={
            "message": "compare these notes",
            "session_id": "member-7",
            "username": "Member",
            "chat_type": "group",
            "group_id": "study-room",
            "agent_id": "selected-agent",
        },
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["agent_id"] == "selected-agent"
    assert payload["session_key"] == (
        "webui/webui/webui/group:study-room/member-7"
    )
    context, message, options = runtime.calls[0]
    assert message.sender.chat_type is ChatType.GROUP
    assert context.conversation_scope == "group:study-room"
    assert options["session_agent_id"] == "selected-agent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"message": "", "session_id": "session-a"},
        {"message": "hello", "session_id": ""},
        {"message": "hello", "session_id": "session-a", "chat_type": "group"},
    ],
)
async def test_webui_chat_rejects_invalid_channel_identity(chat_app, body):
    app, runtime = chat_app
    client = app.test_client()

    response = await client.post(
        "/api/llm/chat",
        headers={"Authorization": "Bearer mock_token"},
        json=body,
    )

    assert response.status_code == 400
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_webui_chat_requires_an_agent_instead_of_running_fallback_workflow(
    chat_app_without_agent,
):
    app, runtime, workflow_run = chat_app_without_agent
    client = app.test_client()

    response = await client.post(
        "/api/llm/chat",
        headers={"Authorization": "Bearer mock_token"},
        json={
            "message": "compare these notes",
            "session_id": "member-7",
            "chat_type": "group",
            "group_id": "study-room",
        },
    )

    assert response.status_code == 409
    # 文案由 `_NO_AGENT_MESSAGE` 统一给出（原来是英文，用户看不出该做什么）。
    # 断言用共享常量而不是逐字重抄：重抄一份会在下一次改进文案时变成两处真相，
    # 而那时红的是测试、改的是字符串，等于什么都没验证。
    assert await response.get_json() == {"error": _NO_AGENT_MESSAGE}
    # 关键信息必须在：告诉用户去哪配。
    assert "Agent 管理" in _NO_AGENT_MESSAGE
    assert runtime.calls == []
    workflow_run.assert_not_called()

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
            config={**SENSITIVE_CONFIG, "model": "test-model"},
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
    web_server.app.state.container = container
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
        assert_backend_secrets_redacted(backends[0], *SENSITIVE_CONFIG.values())
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
        assert_backend_secrets_redacted(backend, *SENSITIVE_CONFIG.values())
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
            config={
                **SENSITIVE_CONFIG,
                "api_key": "new-key",
                "model": "new-model",
            },
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
            assert_backend_secrets_redacted(
                backend,
                "new-key",
                "test-token",
                "test-authorization",
                "test-cookie",
                "test-password-value",
                "test-client-secret",
                "test-credential",
                "test-refresh-token",
            )
            assert_resilience_settings(backend)

            # 验证配置保存
            mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_backend(self, test_client, auth_headers, monkeypatch):
        """测试更新后端"""
        updated_secrets = {
            **SENSITIVE_CONFIG,
            "api_key": "updated-key",
            "nested": {"refresh_token": "updated-refresh-token"},
        }
        updated_config = LLMBackendConfig(
            name=TEST_BACKEND_NAME,
            adapter=TEST_ADAPTER_TYPE,
            config={**updated_secrets, "model": "updated-model"},
            enable=True,
            models=["updated-model"],
            **RESILIENCE_SETTINGS,
        )

        # Mock 配置文件保存。
        #
        # 用 `monkeypatch.setattr` 而不是直接赋值：`ConfigLoader` 是类对象，
        # 直接赋值会把这个 MagicMock 永久留在类上，后续任何真的需要落盘的用例
        # 都会静默地什么都不写——症状出现在别的测试文件里，且只在按目录整体跑时
        # 才出现，是最难定位的一类测试污染。
        monkeypatch.setattr(ConfigLoader, "save_config_with_backup", MagicMock())
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
        assert_backend_secrets_redacted(
            backend,
            "updated-key",
            "updated-refresh-token",
            "test-token",
            "test-authorization",
            "test-cookie",
            "test-password-value",
            "test-client-secret",
            "test-credential",
        )
        assert_resilience_settings(backend)

        # 验证配置保存
        ConfigLoader.save_config_with_backup.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_backend_keeps_fields_the_client_never_sent(
        self, app, test_client, auth_headers
    ):
        """PUT 里没出现的字段必须保留原值，而不是被重置为模型默认。

        `LLMBackendUpdateRequest` 继承 `LLMBackendConfig`，因此**任何**没被前端
        表单声明的字段都会被 pydantic 用默认值补齐，然后原样写回磁盘。
        于是「在 config.yaml 里开了某个开关，之后在 WebUI 改任意一项」
        会把那个开关静默关掉——用户改的是 A，被改掉的是 B，
        而界面上没有任何地方提示 B 变了。

        这不是某一个字段的疏漏：它对每一个前端还没做控件的字段都成立，
        也对将来新增的每一个字段成立。因此修在合并逻辑上，而不是逐字段补控件。
        """
        config = app.state.container.resolve(GlobalConfig)
        current = next(
            backend
            for backend in config.llms.api_backends
            if backend.name == TEST_BACKEND_NAME
        )
        # 这两项后端有真实消费点，但前端表单里没有控件（`webui/src` 零命中）。
        current.hide_ai_attribution = True
        current.priority = 7

        # 一个「只改启用状态」的最小 payload——真实前端提交也不会带上它不认识的键。
        update = {
            "name": TEST_BACKEND_NAME,
            "adapter": current.adapter,
            "config": current.config,
            "enable": current.enable,
            "models": [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in current.models
            ],
        }

        with patch.object(ConfigLoader, "save_config_with_backup"):
            response = test_client.put(
                f"/backend-api/api/llm/backends/{TEST_BACKEND_NAME}",
                headers=auth_headers,
                json=update,
            )

        assert response.status_code == 200
        saved = next(
            backend
            for backend in config.llms.api_backends
            if backend.name == TEST_BACKEND_NAME
        )
        assert saved.hide_ai_attribution is True, (
            "客户端没提交 hide_ai_attribution，它被重置成了默认值 False"
        )
        assert saved.priority == 7, "客户端没提交 priority，它被重置成了默认值"

    @pytest.mark.asyncio
    async def test_update_backend_can_still_turn_a_flag_off(
        self, app, test_client, auth_headers
    ):
        """显式提交 `false` 与「没提交」必须区分开。

        只保留「没提交的字段」是不够的：如果把 `false` 也当成「没提交」，
        开关就变成了单向的——只能开、不能关。那比被静默重置更糟，
        因为用户会反复点一个不起作用的控件。
        """
        config = app.state.container.resolve(GlobalConfig)
        current = next(
            backend
            for backend in config.llms.api_backends
            if backend.name == TEST_BACKEND_NAME
        )
        current.hide_ai_attribution = True

        update = {
            "name": TEST_BACKEND_NAME,
            "adapter": current.adapter,
            "config": current.config,
            "enable": current.enable,
            "models": [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in current.models
            ],
            "hide_ai_attribution": False,
        }

        with patch.object(ConfigLoader, "save_config_with_backup"):
            response = test_client.put(
                f"/backend-api/api/llm/backends/{TEST_BACKEND_NAME}",
                headers=auth_headers,
                json=update,
            )

        assert response.status_code == 200
        saved = next(
            backend
            for backend in config.llms.api_backends
            if backend.name == TEST_BACKEND_NAME
        )
        assert saved.hide_ai_attribution is False

    @pytest.mark.asyncio
    async def test_update_backend_blank_secret_placeholders_keep_existing_values(
        self, app, test_client, auth_headers
    ):
        config = app.state.container.resolve(GlobalConfig)
        current = next(
            backend
            for backend in config.llms.api_backends
            if backend.name == TEST_BACKEND_NAME
        )
        existing_api_key = current.config["api_key"]
        existing_refresh_token = current.config["nested"]["refresh_token"]
        update = current.model_dump()
        update["config"]["api_key"] = ""
        update["config"]["nested"]["refresh_token"] = ""

        with patch.object(ConfigLoader, "save_config_with_backup"):
            response = test_client.put(
                f"/backend-api/api/llm/backends/{TEST_BACKEND_NAME}",
                headers=auth_headers,
                json=update,
            )

        assert response.status_code == 200
        assert_backend_secrets_redacted(response.json()["data"])
        stored = next(
            backend
            for backend in config.llms.api_backends
            if backend.name == TEST_BACKEND_NAME
        )
        assert stored.config["api_key"] == existing_api_key
        assert stored.config["nested"]["refresh_token"] == existing_refresh_token

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
    async def test_delete_backend(self, test_client, auth_headers, monkeypatch):
        """测试删除后端"""
        save_mock = MagicMock()
        monkeypatch.setattr(ConfigLoader, "save_config_with_backup", save_mock)
        response = test_client.delete(
            f"/backend-api/api/llm/backends/{TEST_BACKEND_NAME}", headers=auth_headers
        )

        data = response.json()
        assert not data.get("error")
        assert "data" in data
        backend = data.get("data")
        assert backend.get("name") == TEST_BACKEND_NAME
        assert_backend_secrets_redacted(backend)
        assert_resilience_settings(backend)
        save_mock.assert_called_once()

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
