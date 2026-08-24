from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_sources import (
    ResourceSourceError,
    ResourceSourceService,
)


def _service(tmp_path: Path) -> ResourceLifecycleService:
    return ResourceLifecycleService(tmp_path / "data")


def _github_archive(*, body: str = "Use sources carefully.") -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "demo-repo-main/skills/research/SKILL.md",
            "---\nname: Research helper\ndescription: Finds reliable evidence\n---\n"
            + body,
        )
        archive.writestr("demo-repo-main/skills/research/README.md", "docs")
        archive.writestr("demo-repo-main/README.md", "repository")
    return payload.getvalue()


def test_remote_source_validation_rejects_unsafe_coordinates_and_urls(tmp_path: Path):
    service = ResourceSourceService(_service(tmp_path))

    with pytest.raises(ResourceSourceError):
        service.validate_repository("owner", "repo", "../releases")
    with pytest.raises(ResourceSourceError):
        service.validate_repository("owner", "repo", "main?x=1")
    with pytest.raises(ResourceSourceError):
        service.validate_remote_url("http://github.com/owner/repo")
    with pytest.raises(ResourceSourceError):
        service.validate_remote_url("https://127.0.0.1/private")


def test_repository_discovery_is_anchored_on_skill_md_and_uses_stable_source_key(
    tmp_path: Path, monkeypatch
):
    service = ResourceSourceService(_service(tmp_path))
    monkeypatch.setattr(service, "_download_bytes", lambda _url: _github_archive())

    skills = service.discover_repository("owner", "demo-repo", "main")

    assert len(skills) == 1
    assert skills[0]["source_key"] == "owner/demo-repo:skills/research"
    assert skills[0]["directory"] == "skills/research"
    assert skills[0]["name"] == "Research helper"
    assert skills[0]["description"] == "Finds reliable evidence"


def test_skills_sh_results_are_normalized_and_remote_install_generates_server_manifest(
    tmp_path: Path, monkeypatch
):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    monkeypatch.setattr(
        service,
        "_request_json",
        lambda _url: {
            "query": "research",
            "count": 1,
            "skills": [
                {
                    "id": "owner/demo-repo:skills/research",
                    "skillId": "skills/research",
                    "name": "Research helper",
                    "installs": 42,
                    "source": "owner/demo-repo",
                }
            ],
        },
    )
    monkeypatch.setattr(service, "_download_bytes", lambda _url: _github_archive())

    result = service.search_skills("research", limit=10, offset=0)
    installed = service.install_skill(
        owner="owner", name="demo-repo", branch="main", directory="skills/research"
    )

    assert result["total_count"] == 1
    assert result["skills"][0]["source_key"] == "owner/demo-repo:skills/research"
    assert installed["source_key"] == "owner/demo-repo:skills/research"
    assert installed["enabled"] is False
    manifest = lifecycle.get_resource(installed["resource_id"])["versions"][0]
    assert manifest["source_key"] == "owner/demo-repo:skills/research"
    assert lifecycle.read_entry(installed["resource_id"]) == "---\nname: Research helper\ndescription: Finds reliable evidence\n---\nUse sources carefully."


def test_repository_sources_are_persisted_as_server_state(tmp_path: Path):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)

    service.add_repository("owner", "repo", "main")
    service.add_repository("owner", "repo", "main")
    service.set_repository_enabled("owner", "repo", "main", False)

    restarted = ResourceSourceService(_service(tmp_path))
    repositories = restarted.list_repositories()

    assert repositories == [
        {"owner": "owner", "name": "repo", "branch": "main", "enabled": False}
    ]


def test_repository_update_check_compares_server_content_hash(tmp_path: Path, monkeypatch):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    archive = _github_archive()
    monkeypatch.setattr(service, "_download_bytes", lambda _url: archive)
    installed = service.install_skill(
        owner="owner", name="demo-repo", branch="main", directory="skills/research"
    )

    current = service.check_updates(installed["resource_id"])
    monkeypatch.setattr(
        service,
        "_download_bytes",
        lambda _url: _github_archive(body="Use current, cited sources."),
    )
    changed = service.check_updates(installed["resource_id"])

    assert current[0]["update_available"] is False
    assert changed[0]["update_available"] is True
    assert changed[0]["next_version"] == "1.0.1"
    assert changed[0]["remote_content_sha256"] != changed[0]["current_content_sha256"]


def test_repository_update_creates_backup_and_publishes_new_version(tmp_path: Path, monkeypatch):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    monkeypatch.setattr(service, "_download_bytes", lambda _url: _github_archive())
    installed = service.install_skill(
        owner="owner", name="demo-repo", branch="main", directory="skills/research"
    )
    monkeypatch.setattr(
        service,
        "_download_bytes",
        lambda _url: _github_archive(body="Use current, cited sources."),
    )

    updated = service.update_skill(installed["resource_id"])

    assert updated["current_version"] == "1.0.1"
    assert lifecycle.read_entry(installed["resource_id"]).endswith(
        "Use current, cited sources."
    )
    assert lifecycle.list_backups(installed["resource_id"])[0]["reason"] == "before-update"
    assert not list(lifecycle.imports_path.glob("remote-update-*.zip"))


def test_failed_repository_update_keeps_registered_version(tmp_path: Path, monkeypatch):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    monkeypatch.setattr(service, "_download_bytes", lambda _url: _github_archive())
    installed = service.install_skill(
        owner="owner", name="demo-repo", branch="main", directory="skills/research"
    )
    before = lifecycle.get_resource(installed["resource_id"])
    monkeypatch.setattr(
        service,
        "_download_bytes",
        lambda _url: _github_archive(body="Changed remote instructions."),
    )

    def fail_registry_write(_registry):
        raise OSError("simulated registry write failure")

    monkeypatch.setattr(lifecycle, "_write_registry", fail_registry_write)
    with pytest.raises(OSError):
        service.update_skill(installed["resource_id"])

    assert lifecycle.get_resource(installed["resource_id"]) == before
    assert lifecycle.read_entry(installed["resource_id"]).endswith(
        "Use sources carefully."
    )
    assert not list(lifecycle.imports_path.glob("remote-update-*.zip"))
