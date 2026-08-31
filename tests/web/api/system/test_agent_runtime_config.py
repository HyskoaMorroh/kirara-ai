"""Agent 运行时的四个参数必须能从界面配，而不只能改 config.yaml。

需求 21.3 要求「重试、超时和熔断参数必须集中配置并校验边界」，并点名
**请求总截止时间**与**取消传播**。`agent_runtime.turn_deadline_seconds`
已经真的把 deadline 与取消信号下传给模型调用——但通往它的路只有一条：
手改 config.yaml。这与修好之前的 `update.disable_auto_check` 是同一种缺陷：
后端读得到、前端到不了。

同一段配置里另有三项处境相同：

- ``reply_stream_mode``（进程默认取回方式）；
- ``channel_reply_stream_modes``（按渠道覆盖）；
- ``tool_search_threshold``（工具渐进披露阈值）。

四项都有真实消费点（`entry.py` 读它们建 executor），四项都没有任何 HTTP
写入路径。一个只能靠登服务器改 YAML 的「集中配置」不满足 21.3——
它恰恰是需求要消除的那种状态。

边界校验必须在**路由层**做出可读的 400，而不是让 pydantic 在落盘阶段抛出
一个 500：前者告诉用户哪一项越界，后者只告诉他「服务器错误」。
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

ENDPOINT = "/backend-api/api/system/config/agent-runtime"


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
    """拦落盘，返回被保存的 config 引用。"""
    from kirara_ai.web.api.system import routes as system_routes

    calls: list[GlobalConfig] = []
    monkeypatch.setattr(
        system_routes.ConfigLoader,
        "save_config_with_backup",
        lambda _path, cfg: calls.append(cfg),
    )
    return calls


class TestAgentRuntimeConfigIsReadable:
    def test_get_config_reports_all_four_fields(self, test_client, auth_headers):
        """读不到当前值，界面就只能猜——四个输入框会显示它猜的那个值。"""
        response = test_client.get(
            "/backend-api/api/system/config", headers=auth_headers
        )
        assert response.status_code == 200
        section = response.json()["agent_runtime"]
        assert section["turn_deadline_seconds"] == 0.0
        assert section["reply_stream_mode"] == "off"
        assert section["channel_reply_stream_modes"] == {}
        assert section["tool_search_threshold"] == 12


class TestAgentRuntimeConfigIsWritable:
    def test_turn_deadline_can_be_set(
        self, test_client, auth_headers, container, saved
    ):
        response = test_client.post(
            ENDPOINT,
            json={"turn_deadline_seconds": 120.0},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert container.resolve(GlobalConfig).agent_runtime.turn_deadline_seconds == 120.0
        assert saved, "配置没有落盘，重启后改动会丢"

    def test_zero_turn_deadline_is_accepted_as_no_budget(
        self, test_client, auth_headers, container, saved
    ):
        container.resolve(GlobalConfig).agent_runtime.turn_deadline_seconds = 90.0

        response = test_client.post(
            ENDPOINT, json={"turn_deadline_seconds": 0}, headers=auth_headers
        )

        # 0 是「不设总预算」这个有意义的值，不能被当成「没填」而忽略。
        assert response.status_code == 200
        assert container.resolve(GlobalConfig).agent_runtime.turn_deadline_seconds == 0.0

    def test_reply_stream_mode_can_be_set(
        self, test_client, auth_headers, container, saved
    ):
        response = test_client.post(
            ENDPOINT, json={"reply_stream_mode": "aggregate"}, headers=auth_headers
        )
        assert response.status_code == 200
        assert container.resolve(GlobalConfig).agent_runtime.reply_stream_mode == "aggregate"

    def test_channel_reply_stream_modes_can_be_set(
        self, test_client, auth_headers, container, saved
    ):
        response = test_client.post(
            ENDPOINT,
            json={"channel_reply_stream_modes": {"telegram": "incremental"}},
            headers=auth_headers,
        )
        assert response.status_code == 200
        stored = container.resolve(GlobalConfig).agent_runtime.channel_reply_stream_modes
        assert stored == {"telegram": "incremental"}

    def test_tool_search_threshold_can_be_set(
        self, test_client, auth_headers, container, saved
    ):
        response = test_client.post(
            ENDPOINT, json={"tool_search_threshold": 40}, headers=auth_headers
        )
        assert response.status_code == 200
        assert container.resolve(GlobalConfig).agent_runtime.tool_search_threshold == 40

    def test_zero_threshold_turns_tool_search_off(
        self, test_client, auth_headers, container, saved
    ):
        response = test_client.post(
            ENDPOINT, json={"tool_search_threshold": 0}, headers=auth_headers
        )

        # 0 表示关闭渐进披露，是文档写明的合法值，不能被边界校验拒掉。
        assert response.status_code == 200
        assert container.resolve(GlobalConfig).agent_runtime.tool_search_threshold == 0


class TestOmittedKeysKeepTheirValues:
    def test_omitted_fields_are_not_reset(
        self, test_client, auth_headers, container, saved
    ):
        runtime = container.resolve(GlobalConfig).agent_runtime
        runtime.turn_deadline_seconds = 300.0
        runtime.tool_search_threshold = 25
        runtime.reply_stream_mode = "aggregate"

        response = test_client.post(
            ENDPOINT, json={"turn_deadline_seconds": 60.0}, headers=auth_headers
        )

        # 「改一个字段」不能把其余调好的字段重置回出厂值——这是 LLMBackend
        # 那批字段真实发生过的缺陷，同一个坑不能在这里重挖一遍。
        assert response.status_code == 200
        runtime = container.resolve(GlobalConfig).agent_runtime
        assert runtime.turn_deadline_seconds == 60.0
        assert runtime.tool_search_threshold == 25
        assert runtime.reply_stream_mode == "aggregate"

    def test_empty_body_changes_nothing_and_does_not_crash(
        self, test_client, auth_headers, container
    ):
        response = test_client.post(ENDPOINT, json={}, headers=auth_headers)

        assert response.status_code == 200
        assert container.resolve(GlobalConfig).agent_runtime.turn_deadline_seconds == 0.0


class TestBoundaryValidationIsReadable:
    def test_negative_turn_deadline_is_rejected_with_400(
        self, test_client, auth_headers, container
    ):
        response = test_client.post(
            ENDPOINT, json={"turn_deadline_seconds": -1}, headers=auth_headers
        )

        # 400 而不是 500：用户需要知道是哪一项越界，而不是「服务器错误」。
        assert response.status_code == 400
        assert container.resolve(GlobalConfig).agent_runtime.turn_deadline_seconds == 0.0

    def test_turn_deadline_above_the_ceiling_is_rejected(
        self, test_client, auth_headers
    ):
        response = test_client.post(
            ENDPOINT, json={"turn_deadline_seconds": 3601}, headers=auth_headers
        )
        assert response.status_code == 400

    def test_unknown_reply_stream_mode_is_rejected(self, test_client, auth_headers):
        response = test_client.post(
            ENDPOINT, json={"reply_stream_mode": "streaming"}, headers=auth_headers
        )
        # 写错的档位一律拒绝：静默当成「关闭」会让用户以为已经开了。
        assert response.status_code == 400

    def test_inherit_is_not_a_valid_process_default(self, test_client, auth_headers):
        response = test_client.post(
            ENDPOINT, json={"reply_stream_mode": "inherit"}, headers=auth_headers
        )
        # `inherit` 只在 Agent 层有意义：进程默认没有上层可继承。
        assert response.status_code == 400

    def test_unknown_channel_mode_is_rejected(self, test_client, auth_headers):
        response = test_client.post(
            ENDPOINT,
            json={"channel_reply_stream_modes": {"telegram": "nope"}},
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_negative_threshold_is_rejected(self, test_client, auth_headers):
        response = test_client.post(
            ENDPOINT, json={"tool_search_threshold": -1}, headers=auth_headers
        )
        assert response.status_code == 400

    def test_unknown_key_is_rejected_instead_of_silently_dropped(
        self, test_client, auth_headers
    ):
        response = test_client.post(
            ENDPOINT, json={"turn_deadline_secondz": 60}, headers=auth_headers
        )
        # 打错的键被静默丢掉时，界面会显示「保存成功」而那个值从未生效。
        assert response.status_code == 400

    def test_non_object_body_is_rejected(self, test_client, auth_headers):
        response = test_client.post(ENDPOINT, json=[1, 2], headers=auth_headers)
        assert response.status_code == 400


class TestAuthorizationIsRequired:
    def test_unauthenticated_request_is_rejected(self, test_client):
        response = test_client.post(ENDPOINT, json={"turn_deadline_seconds": 60})
        assert response.status_code in {401, 403}
