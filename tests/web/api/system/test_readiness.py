import asyncio
import threading
import time
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


@pytest.mark.asyncio
async def test_readiness_fails_for_invalid_user_owned_files_without_values(tmp_path):
    config, workflows, dispatch, im_manager, llm_manager, mcp_manager = (
        _readiness_dependencies(tmp_path)
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llms: not-a-mapping\n", encoding="utf-8")

    workflow_dir = tmp_path / "workflows"
    (workflow_dir / "chat").mkdir(parents=True)
    (workflow_dir / "chat" / "broken.yaml").write_text("blocks: [\n", encoding="utf-8")
    workflows.workflows_dir = str(workflow_dir)
    workflows.container = DependencyContainer()

    rules_dir = tmp_path / "dispatch_rules"
    rules_dir.mkdir()
    (rules_dir / "broken.yaml").write_text("- 17\n", encoding="utf-8")
    dispatch.rules_dir = str(rules_dir)

    result = await run_readiness_checks(
        config,
        workflows,
        dispatch,
        im_manager,
        llm_manager,
        mcp_manager,
        data_path=tmp_path,
        config_path=config_path,
        timeout_seconds=0.5,
    )

    by_id = {check.id: check for check in result.checks}
    assert by_id["configuration_parseable"].status == "fail"
    assert by_id["workflows_valid"].status == "fail"
    assert by_id["dispatch_targets_exist"].status == "fail"
    assert "not-a-mapping" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_repeated_slow_readiness_requests_have_fixed_worker_capacity(
    tmp_path, monkeypatch
):
    dependencies = _readiness_dependencies(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    active = 0
    max_active = 0
    lock = threading.Lock()

    def slow_check(*args, **kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        entered.set()
        release.wait(timeout=2)
        with lock:
            active -= 1
        from kirara_ai.web.api.system.readiness import _check

        return _check("data_directories_writable", "pass", "ok", "none")

    monkeypatch.setattr(
        "kirara_ai.web.api.system.readiness._writable_directories", slow_check
    )
    tasks = [
        asyncio.create_task(
            run_readiness_checks(*dependencies, data_path=tmp_path, timeout_seconds=0.01)
        )
        for _ in range(8)
    ]
    await asyncio.to_thread(entered.wait, 1)
    await asyncio.gather(*tasks)
    release.set()
    await asyncio.sleep(0.05)

    assert max_active <= 4
    assert any(
        check.evidence.get("capacity_exhausted")
        for result in [task.result() for task in tasks]
        for check in result.checks
    )


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
