import io
from pathlib import Path
from unittest.mock import patch

import pytest
from werkzeug.datastructures import FileStorage

from kirara_ai.backup.service import BackupService
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService
from tests.backup.test_service import write_project_data


@pytest.fixture
def api_client():
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(AuthService, MockAuthService())
    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client()


def make_upload(archive_path: Path) -> FileStorage:
    return FileStorage(
        stream=io.BytesIO(archive_path.read_bytes()),
        filename=archive_path.name,
        content_type="application/zip",
    )


@pytest.mark.asyncio
async def test_export_backup_requires_authentication(api_client):
    response = await api_client.get("/api/system/backups/export")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_inspect_backup_returns_manifest_without_writing_data(api_client, tmp_path: Path):
    source_data_path = tmp_path / "source-data"
    target_data_path = tmp_path / "target-data"
    write_project_data(source_data_path, "source")
    write_project_data(target_data_path, "target")
    archive_path = BackupService(source_data_path).create_backup()

    with patch(
        "kirara_ai.web.api.system.routes.get_backup_service",
        return_value=BackupService(target_data_path),
    ):
        response = await api_client.post(
            "/api/system/backups/inspect",
            headers={"Authorization": "Bearer mock_token"},
            files={"backup": make_upload(archive_path)},
        )

    assert response.status_code == 200
    payload = await response.get_json()
    assert "workflows" in payload["components"]
    assert (target_data_path / "config.yaml").read_text(encoding="utf-8").endswith("# target\n")


@pytest.mark.asyncio
async def test_import_backup_restores_data_and_requires_restart(api_client, tmp_path: Path):
    source_data_path = tmp_path / "source-data"
    target_data_path = tmp_path / "target-data"
    write_project_data(source_data_path, "source")
    write_project_data(target_data_path, "target")
    archive_path = BackupService(source_data_path).create_backup()

    with patch(
        "kirara_ai.web.api.system.routes.get_backup_service",
        return_value=BackupService(target_data_path),
    ):
        response = await api_client.post(
            "/api/system/backups/import",
            headers={"Authorization": "Bearer mock_token"},
            files={"backup": make_upload(archive_path)},
        )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["restart_required"] is True
    assert payload["rollback_backup"].endswith(".kirara-backup.zip")
    assert (target_data_path / "config.yaml").read_text(encoding="utf-8").endswith("# source\n")
