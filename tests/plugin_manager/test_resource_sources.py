from __future__ import annotations

import hashlib
import io
import json
import socket
import urllib.request
import zipfile
from pathlib import Path

import pytest

from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_sources import (
    ResourceSourceError,
    ResourceSourceService,
    _ValidatedRedirectHandler,
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


def _archive_with_skill_directories(*directories: str) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for directory in directories:
            archive.writestr(
                f"demo-repo-main/{directory}/SKILL.md",
                f"---\nname: {Path(directory).name}\ndescription: test skill\n---\nbody",
            )
    return payload.getvalue()


def _root_skill_archive() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "demo-repo-main/SKILL.md",
            "---\nname: Root skill\ndescription: root repository skill\n---\nbody",
        )
        archive.writestr("demo-repo-main/scripts/run.js", "console.log('root skill')")
    return payload.getvalue()


def _archive_with_skill_entry(
    *, root: str = "demo-repo-v8", entry_path: str = "skills/demo/skill.md"
) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            f"{root}/{entry_path}",
            "---\nname: Demo skill\ndescription: lower-case entry\n---\nbody",
        )
        directory = str(Path(entry_path).parent).replace("\\", "/")
        archive.writestr(f"{root}/{directory}/README.md", "docs")
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


def test_remote_download_rejects_private_address_after_dns_resolution(
    tmp_path: Path, monkeypatch
):
    service = ResourceSourceService(_service(tmp_path))

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))
        ],
    )

    with pytest.raises(ResourceSourceError, match="address is not public"):
        service._download_bytes("https://github.com/owner/repo/archive/main.zip")


def test_remote_source_accepts_transparent_proxy_dns_for_allowlisted_host(
    tmp_path: Path, monkeypatch
):
    service = ResourceSourceService(_service(tmp_path))
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.223", 443))
        ],
    )

    assert (
        service._validate_network_target("https://skills.sh/api/search?q=research")
        == "https://skills.sh/api/search?q=research"
    )


def test_transparent_proxy_dns_exception_is_limited_to_allowlisted_hosts(
    tmp_path: Path, monkeypatch
):
    service = ResourceSourceService(_service(tmp_path))
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.223", 443))
        ],
    )

    with pytest.raises(ResourceSourceError, match="address is not public"):
        service._validate_resolved_addresses("example.com", 443)


def test_transparent_proxy_dns_does_not_hide_private_resolution(
    tmp_path: Path, monkeypatch
):
    service = ResourceSourceService(_service(tmp_path))
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.223", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
        ],
    )

    with pytest.raises(ResourceSourceError, match="address is not public"):
        service._validate_network_target("https://skills.sh/api/search?q=research")


def test_redirect_target_is_revalidated_for_each_hop(tmp_path: Path, monkeypatch):
    service = ResourceSourceService(_service(tmp_path))
    request = urllib.request.Request("https://github.com/owner/repo/archive/main.zip")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.9", 443))
        ],
    )

    handler = _ValidatedRedirectHandler(service)
    with pytest.raises(ResourceSourceError, match="address is not public"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://raw.githubusercontent.com/owner/repo/main/SKILL.md",
        )


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


def test_missing_branch_is_resolved_from_github_repository_metadata(
    tmp_path: Path, monkeypatch
):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    requested_urls: list[str] = []

    def fake_request_json(url: str):
        requested_urls.append(url)
        return {"default_branch": "v8"}

    monkeypatch.setattr(service, "_request_json", fake_request_json)
    monkeypatch.setattr(
        service,
        "_download_bytes",
        lambda url: (
            requested_urls.append(url) or _archive_with_skill_entry(
                root="graphify-v8", entry_path="graphify/skill.md"
            )
        ),
    )

    installed = service.install_skill(
        owner="graphify-labs", name="graphify", branch=None, directory="graphify"
    )

    assert requested_urls[0] == "https://api.github.com/repos/graphify-labs/graphify"
    assert "/zip/refs/heads/v8" in requested_urls[1]
    assert installed["source_key"] == "graphify-labs/graphify:graphify"
    assert installed["source_metadata"]["branch"] == "v8"
    assert installed["source_metadata"]["directory"] == "graphify"


def test_lowercase_skill_entry_is_normalized_to_standard_skill_md(
    tmp_path: Path, monkeypatch
):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    monkeypatch.setattr(
        service,
        "_download_bytes",
        lambda _url: _archive_with_skill_entry(entry_path="skills/demo/skill.md"),
    )

    installed = service.install_skill(
        owner="owner", name="demo-repo", branch="v8", directory="skills/demo"
    )
    version_path = lifecycle.installed_path / installed["resource_id"] / "1.0.0"

    assert (version_path / "SKILL.md").is_file()
    assert [path.name for path in version_path.iterdir() if path.name.casefold() == "skill.md"] == [
        "SKILL.md"
    ]
    assert lifecycle.read_entry(installed["resource_id"]).startswith("---\nname: Demo skill")


def test_graphify_leaf_directory_resolves_to_the_real_repository_path(
    tmp_path: Path, monkeypatch
):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    monkeypatch.setattr(service, "_request_json", lambda _url: {"default_branch": "v8"})
    monkeypatch.setattr(
        service,
        "_download_bytes",
        lambda _url: _archive_with_skill_entry(
            root="graphify-v8", entry_path="graphify/skill.md"
        ),
    )

    installed = service.install_skill(
        owner="graphify-labs", name="graphify", branch=None, directory="graphify"
    )

    assert installed["source"] == (
        "https://github.com/graphify-labs/graphify/tree/v8/graphify"
    )
    assert installed["source_metadata"]["directory"] == "graphify"
    assert installed["source_metadata"]["branch"] == "v8"


def test_failed_remote_install_cleans_import_and_staging_paths(
    tmp_path: Path, monkeypatch
):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    monkeypatch.setattr(service, "_download_bytes", lambda _url: _github_archive())

    def fail_registry_write(_registry):
        raise OSError("simulated registry write failure")

    monkeypatch.setattr(lifecycle, "_write_registry", fail_registry_write)
    with pytest.raises(OSError):
        service.install_skill(
            owner="owner", name="demo-repo", branch="main", directory="skills/research"
        )

    assert not list(lifecycle.imports_path.glob("remote-*.zip"))
    assert not list(lifecycle.staging_path.iterdir())


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


def test_skills_sh_leaf_directory_is_resolved_to_nested_skill_and_persisted(
    tmp_path: Path, monkeypatch
):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    monkeypatch.setattr(
        service,
        "_download_bytes",
        lambda _url: _archive_with_skill_directories("skills/agent-browser"),
    )

    installed = service.install_skill(
        owner="owner",
        name="demo-repo",
        branch="main",
        directory="agent-browser",
        source_key="owner/demo-repo:agent-browser",
    )

    assert installed["source_key"] == "owner/demo-repo:skills/agent-browser"
    assert installed["source"] == "https://github.com/owner/demo-repo/tree/main/skills/agent-browser"
    assert installed["source_metadata"]["directory"] == "skills/agent-browser"
    manifest = lifecycle.get_resource(installed["resource_id"])["versions"][0]
    assert manifest["source_key"] == "owner/demo-repo:skills/agent-browser"
    assert manifest["source_metadata"]["directory"] == "skills/agent-browser"


def test_leaf_resolution_skips_same_name_wrapper_without_skill_md(tmp_path: Path, monkeypatch):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    monkeypatch.setattr(
        service,
        "_download_bytes",
        lambda _url: _archive_with_skill_directories("agent-browser/skills/agent-browser"),
    )

    installed = service.install_skill(
        owner="owner", name="demo-repo", branch="main", directory="agent-browser"
    )

    assert installed["source_metadata"]["directory"] == "agent-browser/skills/agent-browser"


def test_leaf_resolution_rejects_multiple_matching_skill_directories(
    tmp_path: Path, monkeypatch
):
    service = ResourceSourceService(_service(tmp_path))
    monkeypatch.setattr(
        service,
        "_download_bytes",
        lambda _url: _archive_with_skill_directories("skills/agent-browser", "catalog/agent-browser"),
    )

    with pytest.raises(ResourceSourceError, match="ambiguous"):
        service.install_skill(
            owner="owner", name="demo-repo", branch="main", directory="agent-browser"
        )


def test_complete_directory_is_preferred_over_leaf_fallback(tmp_path: Path, monkeypatch):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    monkeypatch.setattr(
        service,
        "_download_bytes",
        lambda _url: _archive_with_skill_directories("skills/agent-browser", "catalog/agent-browser"),
    )

    installed = service.install_skill(
        owner="owner", name="demo-repo", branch="main", directory="skills/agent-browser"
    )

    assert installed["source_metadata"]["directory"] == "skills/agent-browser"


def test_root_repository_skill_uses_root_identity_and_includes_nested_files(
    tmp_path: Path, monkeypatch
):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    monkeypatch.setattr(service, "_download_bytes", lambda _url: _root_skill_archive())

    installed = service.install_skill(
        owner="owner", name="demo-repo", branch="main", directory="root-skill"
    )

    assert installed["source_key"] == "owner/demo-repo:."
    assert installed["source"] == "https://github.com/owner/demo-repo/tree/main"
    assert installed["source_metadata"]["directory"] == "."
    version_path = (
        lifecycle.installed_path / installed["resource_id"] / installed["current_version"]
    )
    assert (version_path / "SKILL.md").is_file()
    assert (version_path / "scripts" / "run.js").is_file()


def test_update_reuses_resolved_nested_directory_metadata(tmp_path: Path, monkeypatch):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)
    monkeypatch.setattr(
        service,
        "_download_bytes",
        lambda _url: _archive_with_skill_directories("skills/agent-browser"),
    )
    installed = service.install_skill(
        owner="owner", name="demo-repo", branch="main", directory="agent-browser"
    )

    updated = service.check_updates(installed["resource_id"])

    assert updated[0]["source_key"] == "owner/demo-repo:skills/agent-browser"
    assert updated[0]["source_metadata"]["directory"] == "skills/agent-browser"


def test_catalog_matches_skills_sh_leaf_alias_to_resolved_installed_directory(
    tmp_path: Path, monkeypatch
):
    lifecycle = _service(tmp_path)
    source = ResourceSourceService(lifecycle)
    catalog = ResourceCatalogService(lifecycle, source)
    monkeypatch.setattr(
        source,
        "_download_bytes",
        lambda _url: _archive_with_skill_directories("skills/agent-browser"),
    )

    installed = source.install_skill(
        owner="vercel-labs",
        name="agent-browser",
        branch="main",
        directory="agent-browser",
    )

    item = {
        "catalog_id": "skill:vercel-labs/agent-browser:agent-browser",
        "type": "skill",
        "source_key": "vercel-labs/agent-browser:agent-browser",
        "owner": "vercel-labs",
        "repository": "agent-browser",
        "branch": "main",
        "directory": "agent-browser",
    }

    assert catalog._installed_for_catalog(item) == installed
    assert catalog._with_install_state(item)["installed"] is True
    assert catalog._with_install_state(item)["installed_resource_id"] == installed["resource_id"]


def test_catalog_skill_install_returns_existing_resource_for_leaf_alias(
    tmp_path: Path, monkeypatch
):
    lifecycle = _service(tmp_path)
    source = ResourceSourceService(lifecycle)
    catalog = ResourceCatalogService(lifecycle, source)
    monkeypatch.setattr(
        source,
        "_download_bytes",
        lambda _url: _archive_with_skill_directories("skills/agent-browser"),
    )

    first = source.install_skill(
        owner="vercel-labs",
        name="agent-browser",
        branch="main",
        directory="agent-browser",
    )
    second = catalog.install("skill:vercel-labs/agent-browser:agent-browser")

    assert second["resource_id"] == first["resource_id"]
    assert len(lifecycle.list_resources("skill")) == 1


def test_repository_sources_are_persisted_as_server_state(tmp_path: Path):
    lifecycle = _service(tmp_path)
    service = ResourceSourceService(lifecycle)

    service.add_repository("owner", "repo", "main")
    service.add_repository("owner", "repo", "main")
    service.set_repository_enabled("owner", "repo", "main", False)

    restarted = ResourceSourceService(_service(tmp_path))
    repositories = restarted.list_repositories()

    # 断言坐标与启用状态，不整体比对字典：新增一个字段（例如
    # `discovered_skills`）不该让这条测试红——它守的是「重复登记只留一条、
    # 状态改动落盘」，与仓库记录有几个字段无关。
    assert len(repositories) == 1
    assert {
        key: repositories[0][key] for key in ("owner", "name", "branch", "enabled")
    } == {"owner": "owner", "name": "repo", "branch": "main", "enabled": False}


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
