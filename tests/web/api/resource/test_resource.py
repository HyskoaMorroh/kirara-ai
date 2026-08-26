import hashlib
import io
import json
import zipfile
import asyncio
import threading
import time
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.mcp_module.models import MCPConnectionState
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_sources import ResourceSourceService
from kirara_ai.plugin_manager.system_dependencies import SystemDependencyService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService
from kirara_ai.workflow.core.block.registry import BlockRegistry


class WorkflowRegistry:
    def get_workflow(self, workflow_id, container):
        return object() if workflow_id == "chat:normal" else None


def _archive(path: Path, *, version: str = "1.0.0", body: str = "resource body") -> Path:
    files = {
        "main.py": body.encode("utf-8"),
        "README.txt": b"metadata",
    }
    records = [
        {
            "path": name,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for name, content in files.items()
    ]
    content_sha256 = hashlib.sha256(
        b"".join(
            f"{item['path']}:{item['size']}:{item['sha256']}\n".encode("ascii")
            for item in sorted(records, key=lambda item: item["path"])
        )
    ).hexdigest()
    manifest = {
        "resource_id": "api.demo",
        "type": "skill",
        "version": version,
        "source": "api-test-source",
        "entry": "main.py",
        "permissions": ["workflow.read"],
        "files": records,
        "content_sha256": content_sha256,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def _upload(path: Path) -> FileStorage:
    return FileStorage(
        stream=io.BytesIO(path.read_bytes()),
        filename=path.name,
        content_type="application/zip",
    )


@pytest.fixture
def resource_api(tmp_path: Path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(AuthService, MockAuthService())
    container.register(EventBus, EventBus())
    container.register(BlockRegistry, BlockRegistry())
    lifecycle = ResourceLifecycleService(
        tmp_path / "data",
        workflow_registry=WorkflowRegistry(),
        container=container,
    )
    container.register(ResourceLifecycleService, lifecycle)
    source_service = ResourceSourceService(lifecycle)
    container.register(ResourceSourceService, source_service)
    dependency_service = SystemDependencyService(tmp_path / "data")
    container.register(SystemDependencyService, dependency_service)
    container.register(
        ResourceCatalogService,
        ResourceCatalogService(lifecycle, source_service, dependency_service),
    )
    manager = MCPServerManager(container, audit_sink=lifecycle.append_runtime_audit)
    container.register(MCPServerManager, manager)
    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), lifecycle, source_service


@pytest.mark.asyncio
async def test_resource_source_api_persists_repository_and_normalizes_search(resource_api, monkeypatch):
    client, _, source = resource_api
    headers = {"Authorization": "Bearer mock_token"}

    added = await client.post(
        "/api/resources/repositories",
        headers=headers,
        json={"owner": "owner", "name": "repo", "branch": "main"},
    )
    assert added.status_code == 201
    assert (await added.get_json())["owner"] == "owner"

    listed = await client.get("/api/resources/repositories", headers=headers)
    assert listed.status_code == 200
    assert (await listed.get_json())[0]["branch"] == "main"

    monkeypatch.setattr(
        source,
        "_request_json",
        lambda _url: {"skills": [{"id": "owner/repo:skills/demo", "source": "owner/repo", "skillId": "skills/demo"}]},
    )
    searched = await client.get(
        "/api/resources/skills-sh/search?q=demo&limit=10&offset=0", headers=headers
    )
    assert searched.status_code == 200
    assert (await searched.get_json())["skills"][0]["source_key"] == "owner/repo:skills/demo"


@pytest.mark.asyncio
async def test_resource_backup_api_lists_and_requires_confirmation(resource_api):
    client, _, _ = resource_api
    response = await client.get(
        "/api/resources/backups", headers={"Authorization": "Bearer mock_token"}
    )
    assert response.status_code == 200
    assert await response.get_json() == []


@pytest.mark.asyncio
async def test_dependency_api_lists_public_status_without_server_commands(resource_api):
    client, lifecycle, _ = resource_api
    response = await client.get(
        "/api/resources/dependencies",
        headers={"Authorization": "Bearer mock_token"},
    )

    assert response.status_code == 200
    payload = await response.get_json()
    browser = next(item for item in payload if item["dependency_id"] == "agent-browser-cli")
    assert browser["install_supported"] is True
    assert browser["status"] == "unknown"
    assert "install_argv" not in json.dumps(payload)
    assert "npm install" not in json.dumps(payload)
    assert str(lifecycle.data_path) not in json.dumps(payload)


@pytest.mark.asyncio
async def test_dependency_install_api_requires_confirmation_and_rejects_command_fields(
    resource_api, monkeypatch
):
    client, lifecycle, _ = resource_api
    dependencies = lifecycle.container.resolve(SystemDependencyService)
    calls = []
    monkeypatch.setattr(
        dependencies,
        "install",
        lambda dependency_id, *, confirmed: calls.append((dependency_id, confirmed))
        or {"task_id": "dep-test", "dependency_id": dependency_id, "status": "queued"},
    )
    headers = {"Authorization": "Bearer mock_token"}

    missing_confirmation = await client.post(
        "/api/resources/dependencies/agent-browser-cli/install",
        headers=headers,
        json={},
    )
    injected = await client.post(
        "/api/resources/dependencies/agent-browser-cli/install",
        headers=headers,
        json={"confirmed": True, "command": "whoami", "env": {"PATH": "attacker"}},
    )
    accepted = await client.post(
        "/api/resources/dependencies/agent-browser-cli/install",
        headers=headers,
        json={"confirmed": True},
    )

    assert missing_confirmation.status_code == 409
    assert injected.status_code == 400
    assert accepted.status_code == 202
    assert calls == [("agent-browser-cli", True)]


@pytest.mark.asyncio
async def test_dependency_task_api_probes_lists_retries_and_cancels(resource_api, monkeypatch):
    client, lifecycle, _ = resource_api
    dependencies = lifecycle.container.resolve(SystemDependencyService)
    headers = {"Authorization": "Bearer mock_token"}
    monkeypatch.setattr(
        dependencies,
        "probe",
        lambda dependency_id: {"dependency_id": dependency_id, "status": "ready", "ready": True},
    )
    monkeypatch.setattr(
        dependencies,
        "list_tasks",
        lambda **kwargs: [{"task_id": "dep-test", "dependency_id": "agent-browser-cli", "status": "failed"}],
    )
    monkeypatch.setattr(
        dependencies,
        "get_task",
        lambda task_id: {"task_id": task_id, "dependency_id": "agent-browser-cli", "status": "failed"},
    )
    monkeypatch.setattr(
        dependencies,
        "retry_task",
        lambda task_id, *, confirmed: {"task_id": "dep-retry", "retry_of": task_id, "status": "queued"},
    )
    monkeypatch.setattr(
        dependencies,
        "cancel_task",
        lambda task_id: {"task_id": task_id, "status": "cancelled"},
    )

    probed = await client.post(
        "/api/resources/dependencies/agent-browser-cli/probe", headers=headers, json={}
    )
    tasks = await client.get("/api/resources/dependency-tasks", headers=headers)
    detail = await client.get("/api/resources/dependency-tasks/dep-test", headers=headers)
    retry = await client.post(
        "/api/resources/dependency-tasks/dep-test/retry",
        headers=headers,
        json={"confirmed": True},
    )
    cancelled = await client.post(
        "/api/resources/dependency-tasks/dep-test/cancel", headers=headers, json={}
    )

    assert probed.status_code == 200
    assert (await probed.get_json())["ready"] is True
    assert tasks.status_code == detail.status_code == 200
    assert retry.status_code == 202
    assert (await retry.get_json())["retry_of"] == "dep-test"
    assert cancelled.status_code == 200
    assert (await cancelled.get_json())["status"] == "cancelled"


@pytest.mark.asyncio
async def test_dependency_endpoints_require_authentication(resource_api):
    client, _, _ = resource_api
    for method, path in (
        (client.get, "/api/resources/dependencies"),
        (client.get, "/api/resources/dependencies/node-runtime"),
        (client.post, "/api/resources/dependencies/node-runtime/probe"),
        (client.post, "/api/resources/dependencies/agent-browser-cli/install"),
        (client.get, "/api/resources/dependency-tasks"),
    ):
        response = await method(path)
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_offline_import_is_staged_below_server_resource_directory(
    resource_api, tmp_path: Path, monkeypatch
):
    client, lifecycle, _ = resource_api
    archive = _archive(tmp_path / "import.zip")
    imported_paths: list[Path] = []
    original_import = lifecycle.import_archive

    def record_import(path: Path):
        imported_paths.append(Path(path).resolve())
        return original_import(path)

    monkeypatch.setattr(lifecycle, "import_archive", record_import)
    response = await client.post(
        "/api/resources/imports",
        headers={"Authorization": "Bearer mock_token"},
        files={"resource": _upload(archive)},
    )

    assert response.status_code == 201
    assert imported_paths[0].parent == lifecycle.imports_path.resolve()
    assert imported_paths[0].exists() is False


@pytest.mark.asyncio
async def test_remote_update_api_checks_and_updates_server_registered_source(
    resource_api, monkeypatch
):
    client, _, source = resource_api
    headers = {"Authorization": "Bearer mock_token"}
    monkeypatch.setattr(
        source,
        "check_updates",
        lambda resource_id=None: [{"resource_id": resource_id, "update_available": True}],
    )
    monkeypatch.setattr(
        source,
        "update_skill",
        lambda resource_id: {"resource_id": resource_id, "current_version": "1.0.1"},
    )

    checked = await client.get(
        "/api/resources/updates?resource_id=remote.skill", headers=headers
    )
    updated = await client.post(
        "/api/resources/remote.skill/update", headers=headers, json={}
    )

    assert checked.status_code == 200
    assert (await checked.get_json())[0]["update_available"] is True
    assert updated.status_code == 200
    assert (await updated.get_json())["current_version"] == "1.0.1"


@pytest.mark.asyncio
async def test_resource_api_requires_authentication(resource_api):
    client, _, _ = resource_api
    response = await client.get("/api/resources")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_resource_storage_status_is_authenticated_and_hides_host_path(resource_api):
    client, lifecycle, _ = resource_api

    unauthenticated = await client.get("/api/resources/storage")
    assert unauthenticated.status_code == 401

    response = await client.get(
        "/api/resources/storage",
        headers={"Authorization": "Bearer mock_token"},
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["mode"] == "server_managed"
    assert payload["resource_root"] == "resources"
    assert payload["install_root"] == "resources/installed"
    assert payload["backup_root"] == "resources/backups"
    assert payload["writable"] is True
    assert str(lifecycle.data_path) not in json.dumps(payload)


@pytest.mark.asyncio
async def test_resource_api_projects_runtime_status_without_persisting_it(
    resource_api, tmp_path: Path
):
    client, lifecycle, _ = resource_api
    headers = {"Authorization": "Bearer mock_token"}
    archive = _archive(tmp_path / "runtime.zip")

    installed = await client.post(
        "/api/resources",
        headers=headers,
        files={"resource": _upload(archive)},
    )
    assert installed.status_code == 201
    installed_payload = await installed.get_json()
    assert installed_payload["status"] == "stopped"
    assert installed_payload["running"] is False
    assert installed_payload["failed"] is False
    assert installed_payload["last_error"] is None
    assert installed_payload["last_checked_at"] is None

    enabled = await client.post(
        "/api/resources/api.demo/enable",
        headers=headers,
        json={"confirmed": True},
    )
    assert enabled.status_code == 200
    enabled_payload = await enabled.get_json()
    assert enabled_payload["enabled"] is True
    assert enabled_payload["status"] == "running"
    assert enabled_payload["running"] is True
    assert enabled_payload["failed"] is False

    listed = await client.get("/api/resources", headers=headers)
    detailed = await client.get("/api/resources/api.demo", headers=headers)
    assert listed.status_code == detailed.status_code == 200
    assert (await listed.get_json())[0]["status"] == "running"
    assert (await detailed.get_json())["status"] == "running"

    persisted = lifecycle.get_resource("api.demo")
    assert "status" not in persisted
    assert "last_error" not in persisted


@pytest.mark.asyncio
async def test_mcp_resource_runtime_status_uses_manager_connection_state(resource_api):
    client, lifecycle, _ = resource_api
    manager = lifecycle.container.resolve(MCPServerManager)
    headers = {"Authorization": "Bearer mock_token"}
    installed = await client.post(
        "/api/resources/catalog/install",
        headers=headers,
        json={"catalog_id": "mcp:context7"},
    )
    assert installed.status_code == 201
    lifecycle.enable("mcp.context7", confirmed=True)

    server = type("Server", (), {})()
    manager.servers["context7"] = server

    server.state = MCPConnectionState.DISCONNECTED
    stopped = await client.get("/api/resources/mcp.context7", headers=headers)
    assert stopped.status_code == 200
    assert (await stopped.get_json())["status"] == "stopped"

    server.state = MCPConnectionState.CONNECTED
    running = await client.get("/api/resources/mcp.context7", headers=headers)
    assert (await running.get_json())["status"] == "running"

    server.state = MCPConnectionState.ERROR
    failed = await client.get("/api/resources/mcp.context7", headers=headers)
    failed_payload = await failed.get_json()
    assert failed_payload["status"] == "failed"
    assert failed_payload["failed"] is True
    assert failed_payload["last_error"]


@pytest.mark.asyncio
async def test_mcp_resource_state_changes_reconcile_without_connecting(
    resource_api, monkeypatch
):
    client, lifecycle, _ = resource_api
    manager = lifecycle.container.resolve(MCPServerManager)
    refresh_calls = []

    async def record_refresh(*, connect=True):
        refresh_calls.append(connect)

    monkeypatch.setattr(manager, "refresh_managed_servers", record_refresh)
    headers = {"Authorization": "Bearer mock_token"}
    installed = await client.post(
        "/api/resources/catalog/install",
        headers=headers,
        json={"catalog_id": "mcp:context7"},
    )
    assert installed.status_code == 201

    enabled = await client.post(
        "/api/resources/mcp.context7/enable",
        headers=headers,
        json={"confirmed": True},
    )
    disabled = await client.post(
        "/api/resources/mcp.context7/disable",
        headers=headers,
        json={},
    )

    assert enabled.status_code == disabled.status_code == 200
    assert refresh_calls == [False, False]


@pytest.mark.asyncio
async def test_resource_api_rejects_uploads_above_the_archive_limit(resource_api, monkeypatch):
    client, _, _ = resource_api
    monkeypatch.setattr(
        "kirara_ai.web.api.resource.routes.MAX_RESOURCE_UPLOAD_SIZE",
        32,
    )
    response = await client.post(
        "/api/resources",
        headers={"Authorization": "Bearer mock_token"},
        files={"resource": FileStorage(io.BytesIO(b"x" * 33), filename="x.zip")},
    )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_resource_api_stages_regular_upload_below_server_resource_directory(
    resource_api, tmp_path: Path, monkeypatch
):
    client, lifecycle, _ = resource_api
    archive = _archive(tmp_path / "staged.zip")
    staged_paths: list[Path] = []
    original_install = lifecycle.install_archive

    def record_install(path):
        staged_paths.append(Path(path).resolve())
        return original_install(path)

    monkeypatch.setattr(lifecycle, "install_archive", record_install)
    response = await client.post(
        "/api/resources",
        headers={"Authorization": "Bearer mock_token"},
        files={"resource": _upload(archive)},
    )

    assert response.status_code == 201
    assert staged_paths[0].parent == lifecycle.imports_path.resolve()
    assert staged_paths[0].exists() is False


@pytest.mark.asyncio
async def test_resource_api_installs_updates_binds_and_restores_with_confirmation(
    resource_api, tmp_path: Path
):
    client, lifecycle, _ = resource_api
    first = _archive(tmp_path / "v1.zip")
    response = await client.post(
        "/api/resources",
        headers={"Authorization": "Bearer mock_token"},
        files={"resource": _upload(first)},
    )
    assert response.status_code == 201
    first_payload = await response.get_json()
    assert first_payload["resource_id"] == "api.demo"
    assert first_payload["enabled"] is False

    denied = await client.post(
        "/api/resources/api.demo/enable",
        headers={"Authorization": "Bearer mock_token"},
        json={},
    )
    assert denied.status_code == 409

    bound = await client.post(
        "/api/resources/api.demo/workflow",
        headers={"Authorization": "Bearer mock_token"},
        json={"workflow_id": "chat:normal"},
    )
    assert bound.status_code == 200
    bound_payload = await bound.get_json()
    assert bound_payload["workflow_id"] == "chat:normal"

    second = _archive(tmp_path / "v2.zip", version="2.0.0", body="new body")
    updated = await client.post(
        "/api/resources/api.demo/versions",
        headers={"Authorization": "Bearer mock_token"},
        files={"resource": _upload(second)},
    )
    assert updated.status_code == 200
    updated_payload = await updated.get_json()
    assert updated_payload["current_version"] == "2.0.0"

    enabled = await client.post(
        "/api/resources/api.demo/enable",
        headers={"Authorization": "Bearer mock_token"},
        json={"confirmed": True},
    )
    assert enabled.status_code == 200
    enabled_payload = await enabled.get_json()
    assert enabled_payload["enabled"] is True

    restored = await client.post(
        "/api/resources/api.demo/restore",
        headers={"Authorization": "Bearer mock_token"},
        json={"version": "1.0.0", "confirmed": True},
    )
    assert restored.status_code == 200
    restored_payload = await restored.get_json()
    assert restored_payload["current_version"] == "1.0.0"
    assert restored_payload["enabled"] is False
    assert lifecycle.get_resource("api.demo")["workflow_id"] == "chat:normal"


@pytest.mark.asyncio
async def test_resource_api_exposes_paginated_audit_without_resource_body(resource_api, tmp_path: Path):
    client, _, _ = resource_api
    archive = _archive(tmp_path / "audit.zip", body="private resource body")
    await client.post(
        "/api/resources",
        headers={"Authorization": "Bearer mock_token"},
        files={"resource": _upload(archive)},
    )

    response = await client.get(
        "/api/resources/audit?offset=0&limit=1",
        headers={"Authorization": "Bearer mock_token"},
    )
    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["limit"] == 1
    assert len(payload["items"]) == 1
    assert "private resource body" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_resource_api_queries_mcp_runtime_events_from_unified_audit(resource_api):
    client, lifecycle, _ = resource_api
    manager = lifecycle.container.resolve(MCPServerManager)
    manager._audit_operation(
        "context7",
        "call_tool",
        time.monotonic(),
        "success",
        correlation_id="api-correlation-123",
    )

    response = await client.get(
        "/api/resources/audit?component=mcp&server=context7&correlation_id=api-correlation-123",
        headers={"Authorization": "Bearer mock_token"},
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["total"] == 1
    assert payload["items"][0]["operation"] == "call_tool"
    assert payload["items"][0]["outcome"] == "success"


@pytest.mark.asyncio
async def test_catalog_search_returns_builtin_resources_and_supports_type_filters(resource_api):
    client, _, _ = resource_api
    headers = {"Authorization": "Bearer mock_token"}

    all_items = await client.get("/api/resources/catalog/search?q=", headers=headers)
    assert all_items.status_code == 200
    all_payload = await all_items.get_json()
    assert {item["catalog_id"] for item in all_payload["items"]} >= {
        "prompt:office-research",
        "memory:research-context",
        "mcp:context7",
        "hook:ai-debug",
    }

    prompts = await client.get(
        "/api/resources/catalog/search?type=prompt&q=office", headers=headers
    )
    assert prompts.status_code == 200
    prompt_payload = await prompts.get_json()
    assert [item["catalog_id"] for item in prompt_payload["items"]] == [
        "prompt:office-research"
    ]
    assert all(item["type"] == "prompt" for item in prompt_payload["items"])

    memories = await client.get(
        "/api/resources/catalog/search?type=memory&q=research", headers=headers
    )
    assert memories.status_code == 200
    memory_payload = await memories.get_json()
    assert [item["catalog_id"] for item in memory_payload["items"]] == [
        "memory:research-context"
    ]
    assert memory_payload["items"][0]["installed"] is False


@pytest.mark.asyncio
async def test_catalog_and_installed_resources_project_vps_dependency_readiness(resource_api):
    client, _, _ = resource_api
    headers = {"Authorization": "Bearer mock_token"}

    catalog_response = await client.get(
        "/api/resources/catalog/mcp:context7", headers=headers
    )
    assert catalog_response.status_code == 200
    catalog_item = await catalog_response.get_json()
    assert catalog_item["dependency_ids"] == ["context7-runtime"]
    assert catalog_item["dependency_status"] == "unknown"
    assert catalog_item["dependencies_ready"] is False
    assert catalog_item["system_dependencies"][0]["status"] == "unknown"

    installed_response = await client.post(
        "/api/resources/catalog/install",
        headers=headers,
        json={"catalog_id": "mcp:context7"},
    )
    installed = await installed_response.get_json()
    assert installed["resource_id"] == "mcp.context7"
    assert installed["dependency_ids"] == ["context7-runtime"]
    assert installed["dependencies_ready"] is False
    assert installed["status"] == "stopped"


@pytest.mark.asyncio
async def test_agent_browser_catalog_result_declares_cli_and_browser_runtime_dependencies(
    resource_api, monkeypatch
):
    client, _, source = resource_api
    monkeypatch.setattr(
        source,
        "search_skills",
        lambda query, *, limit, offset: {
            "query": query,
            "skills": [
                {
                    "source_key": "vercel-labs/agent-browser:skills/agent-browser",
                    "owner": "vercel-labs",
                    "repository": "agent-browser",
                    "branch": "main",
                    "directory": "skills/agent-browser",
                    "name": "agent-browser",
                    "description": "Browser automation Skill",
                    "source_url": "https://example.invalid/agent-browser",
                    "installs": 19,
                }
            ],
            "total_count": 1,
            "limit": limit,
            "offset": offset,
        },
    )

    response = await client.get(
        "/api/resources/catalog/search?type=skill&q=agent-browser",
        headers={"Authorization": "Bearer mock_token"},
    )
    assert response.status_code == 200
    item = (await response.get_json())["items"][0]
    assert item["dependency_ids"] == ["agent-browser-cli", "agent-browser-browser"]
    assert item["dependency_status"] == "unknown"


@pytest.mark.asyncio
async def test_catalog_detail_does_not_return_prompt_body(resource_api):
    client, _, _ = resource_api
    response = await client.get(
        "/api/resources/catalog/prompt:office-research",
        headers={"Authorization": "Bearer mock_token"},
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["catalog_id"] == "prompt:office-research"
    assert "content" not in payload
    assert "你是一名面向上班族" not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_catalog_install_is_disabled_by_default_and_idempotent(resource_api):
    client, lifecycle, _ = resource_api
    headers = {"Authorization": "Bearer mock_token"}
    body = {"catalog_id": "prompt:office-research"}

    first = await client.post("/api/resources/catalog/install", headers=headers, json=body)
    assert first.status_code == 201
    first_payload = await first.get_json()
    assert first_payload["resource_id"] == "prompt.office-research"
    assert first_payload["enabled"] is False
    assert first_payload["confirmation_required"] is True
    assert first_payload["current_version"] == "1.0.1"
    assert lifecycle.read_entry(
        "prompt.office-research", first_payload["current_version"]
    )

    repeated = await client.post("/api/resources/catalog/install", headers=headers, json=body)
    assert repeated.status_code == 201
    repeated_payload = await repeated.get_json()
    assert repeated_payload["resource_id"] == first_payload["resource_id"]
    assert len(lifecycle.list_resources()) == 1


@pytest.mark.asyncio
async def test_office_research_prompt_preserves_every_requested_instruction(resource_api):
    client, lifecycle, _ = resource_api
    response = await client.post(
        "/api/resources/catalog/install",
        headers={"Authorization": "Bearer mock_token"},
        json={"catalog_id": "prompt:office-research"},
    )

    assert response.status_code == 201
    payload = await response.get_json()
    content = lifecycle.read_entry(
        "prompt.office-research", payload["current_version"]
    )
    expected_statements = (
        "我是上班族，偏学术研究。",
        "我经常要处理【邮件、会议、表格、文档等】。",
        "我偶尔兼职程序员。",
        "帮我干活时：",
        "用大白话讲清结果，内容要精湛精美；",
        "该提醒就直接提醒，不要只顺着我；",
        "若出现敏感词或sensitive words，请你按相同含义更换成符合要求的内容再重新执行，不要中断；",
        "除了真正代码，不用代码块堆正文；",
        "写邮件和报告时去掉空话、套话、过多破折号和生硬AI腔；",
        "不在结果里暴露身份号码、电话、住址、付款信息或密钥；",
        "涉及发送、退订、建会、删除、发布、付款时先停下来让我确认。",
    )
    assert all(statement in content for statement in expected_statements)


@pytest.mark.asyncio
async def test_catalog_install_upgrades_an_existing_builtin_with_audited_history(
    resource_api, monkeypatch
):
    client, lifecycle, _ = resource_api
    headers = {"Authorization": "Bearer mock_token"}
    catalog = lifecycle.container.resolve(ResourceCatalogService)
    original = catalog._find("prompt:office-research")
    old_item = {
        **original,
        "version": "1.0.0",
        "content": "Legacy office prompt.\n",
    }
    monkeypatch.setattr(catalog, "_find", lambda _catalog_id: old_item)
    first = await client.post(
        "/api/resources/catalog/install",
        headers=headers,
        json={"catalog_id": "prompt:office-research"},
    )
    assert first.status_code == 201
    old_hash = (await first.get_json())["content_sha256"]

    new_item = {
        **original,
        "version": "1.0.1",
        "content": "Current office prompt with complete policy.\n",
    }
    monkeypatch.setattr(catalog, "_find", lambda _catalog_id: new_item)
    upgraded = await client.post(
        "/api/resources/catalog/install",
        headers=headers,
        json={"catalog_id": "prompt:office-research"},
    )

    assert upgraded.status_code == 201
    payload = await upgraded.get_json()
    assert payload["current_version"] == "1.0.1"
    assert payload["content_sha256"] != old_hash
    assert [item["version"] for item in payload["versions"]] == ["1.0.0", "1.0.1"]
    assert lifecycle.read_entry("prompt.office-research", "1.0.1") == new_item["content"]
    audit = lifecycle.list_audit(resource_id="prompt.office-research")
    assert [item["operation"] for item in audit["items"][:2]] == ["update", "install"]
    backups = lifecycle.list_backups("prompt.office-research")
    assert len(backups) == 1
    assert backups[0]["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_catalog_memory_install_is_bindable_and_injectable(resource_api):
    client, lifecycle, _ = resource_api
    headers = {"Authorization": "Bearer mock_token"}

    installed = await client.post(
        "/api/resources/catalog/install",
        headers=headers,
        json={"catalog_id": "memory:research-context"},
    )

    assert installed.status_code == 201
    payload = await installed.get_json()
    assert payload["resource_id"] == "memory.research-context"
    assert payload["type"] == "memory"
    assert lifecycle.read_entry("memory.research-context", "1.0.0")


@pytest.mark.asyncio
async def test_catalog_skill_install_preserves_validated_branch(resource_api, monkeypatch):
    client, _, source = resource_api
    headers = {"Authorization": "Bearer mock_token"}
    monkeypatch.setattr(
        source,
        "search_skills",
        lambda query, *, limit, offset: {
            "query": query,
            "skills": [
                {
                    "source_key": "owner/repository:skills/demo",
                    "owner": "owner",
                    "repository": "repository",
                    "branch": "release-2026",
                    "directory": "skills/demo",
                    "name": "Demo Skill",
                    "description": "A test skill",
                    "source_url": "https://example.invalid/skill",
                    "installs": 4,
                }
            ],
            "total_count": 1,
            "limit": limit,
            "offset": offset,
        },
    )
    calls = []

    def fake_install_skill(**kwargs):
        calls.append(kwargs)
        return {"resource_id": "skill.demo", "enabled": False}

    monkeypatch.setattr(source, "install_skill", fake_install_skill)
    searched = await client.get(
        "/api/resources/catalog/search?q=demo", headers=headers
    )
    assert searched.status_code == 200
    item = (await searched.get_json())["items"][0]
    assert item["branch"] == "release-2026"

    installed = await client.post(
        "/api/resources/catalog/install",
        headers=headers,
        json={"catalog_id": item["catalog_id"], "branch": item["branch"]},
    )
    assert installed.status_code == 201
    assert calls == [
        {
            "owner": "owner",
            "name": "repository",
            "branch": "release-2026",
            "directory": "skills/demo",
            "source_key": "owner/repository:skills/demo",
        }
    ]


@pytest.mark.asyncio
async def test_catalog_search_does_not_apply_remote_offset_twice(resource_api, monkeypatch):
    client, _, source = resource_api
    monkeypatch.setattr(
        source,
        "search_skills",
        lambda query, *, limit, offset: {
            "query": query,
            "skills": [
                {
                    "source_key": "owner/repository:skills/page-2",
                    "owner": "owner",
                    "repository": "repository",
                    "branch": "main",
                    "directory": "skills/page-2",
                    "name": "Page Two",
                    "description": "page result",
                    "installs": 1,
                }
            ],
            "total_count": 3,
            "limit": limit,
            "offset": offset,
        },
    )

    response = await client.get(
        "/api/resources/catalog/search?q=page&limit=1&offset=1",
        headers={"Authorization": "Bearer mock_token"},
    )
    assert response.status_code == 200
    payload = await response.get_json()
    assert [item["name"] for item in payload["items"]] == ["Page Two"]


@pytest.mark.asyncio
async def test_catalog_remote_search_does_not_block_local_api_requests(resource_api, monkeypatch):
    client, _, source = resource_api
    released = threading.Event()
    release_timer = threading.Timer(0.6, released.set)

    def blocking_search(query, *, limit, offset):
        released.wait(timeout=2)
        return {
            "query": query,
            "skills": [],
            "total_count": 0,
            "limit": limit,
            "offset": offset,
        }

    monkeypatch.setattr(source, "search_skills", blocking_search)
    release_timer.start()
    search_task = asyncio.create_task(
        client.get(
            "/api/resources/catalog/search?type=skill&q=agent-browser",
            headers={"Authorization": "Bearer mock_token"},
        )
    )
    storage_started = time.monotonic()
    storage_task = asyncio.create_task(
        client.get(
            "/api/resources/storage",
            headers={"Authorization": "Bearer mock_token"},
        )
    )
    try:
        storage = await storage_task
        assert storage.status_code == 200
        assert time.monotonic() - storage_started < 0.45
    finally:
        released.set()
        await search_task
        release_timer.cancel()


@pytest.mark.asyncio
async def test_catalog_endpoints_require_authentication(resource_api):
    client, _, _ = resource_api
    for method, path in (
        (client.get, "/api/resources/catalog/search?q=office"),
        (client.get, "/api/resources/catalog/prompt:office-research"),
        (client.post, "/api/resources/catalog/install"),
    ):
        response = await method(path)
        assert response.status_code == 401
