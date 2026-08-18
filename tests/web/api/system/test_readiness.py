import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from kirara_ai.config.global_config import GlobalConfig, WebConfig
from kirara_ai.im.manager import IMManager
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.web.api.system.readiness import CHECK_IDS, run_readiness_checks
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.dispatch import DispatchRuleRegistry
from kirara_ai.workflow.core.block.registry import BlockRegistry
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from tests.utils.auth_test_utils import auth_headers, setup_auth_service  # noqa: F401


def _readiness_dependencies(tmp_path):
    config = GlobalConfig(web=WebConfig(secret_key="do-not-return-this"))
    workflows = MagicMock(spec=WorkflowRegistry)
    workflows.workflows_dir = str(tmp_path / "workflows")
    workflows.snapshot_builders.return_value = ()
    workflows.get.return_value = None

    dispatch = MagicMock(spec=DispatchRuleRegistry)
    dispatch.get_all_rules.return_value = []

    im_manager = MagicMock(spec=IMManager)
    im_manager.adapters = {}
    llm_manager = MagicMock(spec=LLMManager)
    llm_manager.active_backends = {}

    mcp_manager = MagicMock(spec=MCPServerManager)
    mcp_manager.get_statistics.return_value = {
        "total": 0,
        "connected": 0,
        "disconnected": 0,
        "error": 0,
    }
    return config, workflows, dispatch, im_manager, llm_manager, mcp_manager


@pytest.mark.asyncio
async def test_readiness_checks_are_ordered_bounded_and_secret_free(tmp_path):
    dependencies = _readiness_dependencies(tmp_path)
    result = await asyncio.wait_for(
        run_readiness_checks(*dependencies, data_path=tmp_path, timeout_seconds=0.5),
        timeout=5,
    )

    assert [check.id for check in result.checks] == list(CHECK_IDS)
    assert all(check.status in {"pass", "warn", "fail", "skip"} for check in result.checks)
    serialized = result.model_dump_json()
    assert "do-not-return-this" not in serialized
    assert all(check.remediation for check in result.checks)


@pytest.fixture
def app(tmp_path):
    container = DependencyContainer()
    config, workflows, dispatch, im_manager, llm_manager, mcp_manager = (
        _readiness_dependencies(tmp_path)
    )
    container.register(GlobalConfig, config)
    container.register(WorkflowRegistry, workflows)
    container.register(DispatchRuleRegistry, dispatch)
    container.register(IMManager, im_manager)
    container.register(LLMManager, llm_manager)
    container.register(MCPServerManager, mcp_manager)
    container.register(BlockRegistry, BlockRegistry())
    setup_auth_service(container)
    web_server = WebServer(container)
    container.register(WebServer, web_server)
    return web_server.app


@pytest.fixture
def test_client(app):
    return TestClient(app)


def test_readiness_endpoint_is_authenticated_and_returns_contract(
    test_client, auth_headers
):
    assert test_client.get("/backend-api/api/system/readiness").status_code == 401

    response = test_client.get(
        "/backend-api/api/system/readiness", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["ready"], bool)
    assert body["timestamp"]
    assert [check["id"] for check in body["checks"]] == list(CHECK_IDS)
