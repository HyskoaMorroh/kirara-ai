import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_sources import ResourceSourceService
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
