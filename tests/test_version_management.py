"""Contracts for the repository-wide version management command."""

import importlib.util
import json
import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_SCRIPT = PROJECT_ROOT / "scripts" / "version.py"


def _load_version_module():
    assert VERSION_SCRIPT.is_file(), "scripts/version.py must be the single version entry point"
    spec = importlib.util.spec_from_file_location("kirara_version_script", VERSION_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("python_version", "npm_version"),
    [
        ("3.3.0", "3.3.0"),
        ("3.3.0a7", "3.3.0-a7"),
        ("3.3.0b8", "3.3.0-b8"),
        ("3.3.0rc1", "3.3.0-rc1"),
    ],
)
def test_pep440_release_versions_are_converted_to_npm_semver(
    python_version, npm_version
):
    version_script = _load_version_module()

    assert version_script.to_npm_version(python_version) == npm_version


@pytest.mark.parametrize("invalid_version", ["", "v3.3.0b8", "3.3", "not-a-version"])
def test_invalid_or_ambiguous_project_versions_are_rejected(invalid_version):
    version_script = _load_version_module()

    with pytest.raises(ValueError):
        version_script.to_npm_version(invalid_version)


def test_set_version_updates_every_generated_version_file_and_refreshes_uv_lock(
    tmp_path, monkeypatch
):
    version_script = _load_version_module()
    (tmp_path / "webui").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b7"\n',
        encoding="utf-8",
    )
    (tmp_path / "webui" / "package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-b7"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b7"\n',
        encoding="utf-8",
    )
    lock_calls = []

    def fake_run(command, **kwargs):
        lock_calls.append((command, kwargs))
        pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        project_version = re.search(r'version = "([^"]+)"', pyproject).group(1)
        (tmp_path / "uv.lock").write_text(
            f'[[package]]\nname = "kirara-ai"\nversion = "{project_version}"\n',
            encoding="utf-8",
        )

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(version_script.subprocess, "run", fake_run)

    version_script.set_version(tmp_path, "3.3.0b8")

    assert 'version = "3.3.0b8"' in (
        tmp_path / "pyproject.toml"
    ).read_text(encoding="utf-8")
    package = json.loads((tmp_path / "webui" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == "3.3.0-b8"
    assert 'version = "3.3.0b8"' in (tmp_path / "uv.lock").read_text(
        encoding="utf-8"
    )
    assert lock_calls
    assert lock_calls[0][0][-1] == "lock"
    assert lock_calls[0][1]["cwd"] == tmp_path
    assert version_script.check_versions(tmp_path) == []


def test_version_artifacts_are_discovered_by_project_package_name(tmp_path):
    version_script = _load_version_module()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "node_modules" / "ignored").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n',
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "package.json").write_text(
        json.dumps({"name": "@kirara/kirara-ai-console", "version": "3.3.0-b8"}),
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "@kirara/kirara-ai-console",
                "version": "3.3.0-b8",
                "packages": {
                    "": {"version": "3.3.0-b8"},
                    "node_modules/unrelated": {"version": "3.3.0"},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "node_modules" / "ignored" / "package.json").write_text(
        json.dumps({"name": "kirara-ai-ignored", "version": "0.0.0"}),
        encoding="utf-8",
    )

    artifacts = {
        path.relative_to(tmp_path).as_posix()
        for path in version_script.discover_version_artifacts(tmp_path)
    }

    assert artifacts == {
        "pyproject.toml",
        "uv.lock",
        "frontend/package.json",
        "frontend/package-lock.json",
    }
    assert version_script.check_versions(tmp_path) == []


def test_manifest_discovery_uses_the_dynamic_python_project_name(tmp_path):
    version_script = _load_version_module()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "research.agent_core"\nversion = "4.2.0rc3"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "research-agent-core"\nversion = "4.2.0rc3"\n',
        encoding="utf-8",
    )
    (tmp_path / "frontend/package.json").write_text(
        json.dumps({"name": "@lab/research-agent-core-console", "version": "4.2.0-rc3"}),
        encoding="utf-8",
    )

    artifacts = {
        path.relative_to(tmp_path).as_posix()
        for path in version_script.discover_version_artifacts(tmp_path)
    }

    assert artifacts == {
        "pyproject.toml",
        "uv.lock",
        "frontend/package.json",
    }
    assert version_script.check_versions(tmp_path) == []


def test_check_reports_each_stale_generated_version(tmp_path):
    version_script = _load_version_module()
    (tmp_path / "webui").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n',
        encoding="utf-8",
    )
    (tmp_path / "webui" / "package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-b7"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b6"\n',
        encoding="utf-8",
    )

    errors = version_script.check_versions(tmp_path)

    assert any("webui/package.json" in error for error in errors)
    assert any("uv.lock" in error for error in errors)


def test_set_scans_active_text_carriers_and_preserves_history(tmp_path, monkeypatch):
    version_script = _load_version_module()
    for directory in ("webui", ".github/workflows", "webui/src", "tests", "build"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0a7"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0a7"\n', encoding="utf-8"
    )
    (tmp_path / "webui/package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-a7"}),
        encoding="utf-8",
    )
    carriers = {
        "README.md": "Install v3.3.0a7\n",
        ".github/workflows/release.yml": "release: 3.3.0a7\n",
        "webui/src/version.ts": "export const version = '3.3.0-a7'\n",
    }
    for relative, contents in carriers.items():
        (tmp_path / relative).write_text(contents, encoding="utf-8")
    ignored = {
        "CHANGELOG.md": "Historical v3.3.0a7\n",
        "tests/fixture.md": "Fixture 3.3.0a7\n",
        "build/generated.txt": "Generated 3.3.0a7\n",
    }
    for relative, contents in ignored.items():
        (tmp_path / relative).write_text(contents, encoding="utf-8")

    def fake_run(command, **kwargs):
        (tmp_path / "uv.lock").write_text(
            '[[package]]\nname = "kirara-ai"\nversion = "3.4.0b1"\n',
            encoding="utf-8",
        )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(version_script.subprocess, "run", fake_run)
    version_script.set_version(tmp_path, "3.4.0b1")

    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "Install v3.4.0b1\n"
    assert (tmp_path / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    ) == "release: 3.4.0b1\n"
    assert (tmp_path / "webui/src/version.ts").read_text(
        encoding="utf-8"
    ) == "export const version = '3.4.0-b1'\n"
    for relative, contents in ignored.items():
        assert (tmp_path / relative).read_text(encoding="utf-8") == contents
    assert version_script.check_versions(tmp_path) == []


def test_check_reports_stale_versions_in_active_text_but_ignores_generated_paths(
    tmp_path,
):
    version_script = _load_version_module()
    (tmp_path / "webui").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "webui/package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-b8"}),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("Still v3.3.0b7\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("History v3.3.0b7\n", encoding="utf-8")
    (tmp_path / "tests/fixture.md").write_text("Fixture v3.3.0b7\n", encoding="utf-8")
    (tmp_path / "logs/run.txt").write_text("Log v3.3.0b7\n", encoding="utf-8")

    errors = version_script.check_versions(tmp_path)

    assert len(errors) == 1
    assert "README.md" in errors[0]


def test_artifact_index_catches_stale_carrier_after_release_base_changes(
    tmp_path, monkeypatch
):
    version_script = _load_version_module()
    (tmp_path / "webui").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "webui/package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-b8"}),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("Install v3.3.0b8\n", encoding="utf-8")

    monkeypatch.setattr(
        version_script.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0})(),
    )
    version_script.set_version(tmp_path, "3.3.0b8")

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.4.0"\n', encoding="utf-8"
    )
    errors = version_script.check_versions(tmp_path)

    assert any("README.md: stale indexed release version(s)" in error for error in errors)


def test_artifact_index_catches_new_current_version_carrier(tmp_path, monkeypatch):
    version_script = _load_version_module()
    (tmp_path / "webui").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "webui/package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-b8"}),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("Install v3.3.0b8\n", encoding="utf-8")
    monkeypatch.setattr(
        version_script.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0})(),
    )
    version_script.set_version(tmp_path, "3.3.0b8")

    (tmp_path / "ops").mkdir()
    (tmp_path / "ops/release.yml").write_text("release: 3.3.0b8\n", encoding="utf-8")

    errors = version_script.check_versions(tmp_path)

    assert errors == [
        ".version-artifacts.json: active artifact is not indexed: ops/release.yml",
    ]


def test_discover_records_active_and_excluded_carriers_with_reasons(tmp_path):
    version_script = _load_version_module()
    (tmp_path / "docs/superpowers/plans").mkdir(parents=True)
    (tmp_path / "ops").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text("Historical v3.3.0b7\n", encoding="utf-8")
    (tmp_path / "docs/superpowers/plans/release.md").write_text(
        "Old plan 3.3.0b7\n", encoding="utf-8"
    )
    (tmp_path / "ops/release.yml").write_text("release: 3.3.0b7\n", encoding="utf-8")

    records = {
        record.path.relative_to(tmp_path).as_posix(): record
        for record in version_script.discover_version_artifact_records(tmp_path)
    }

    assert records["ops/release.yml"].active is True
    assert records["ops/release.yml"].carrier_type == "source-or-config"
    assert records["CHANGELOG.md"].active is False
    assert records["CHANGELOG.md"].exclusion_reason == "historical changelog"
    plan = records["docs/superpowers/plans/release.md"]
    assert plan.active is False
    assert plan.exclusion_reason == "planning and generated task material"
    assert version_script.check_versions(tmp_path) == [
        "ops/release.yml: stale release version(s) [('3.3.0b7', '3.3.0b8')]"
    ]


def test_set_rolls_back_every_touched_file_when_uv_lock_fails(tmp_path, monkeypatch):
    version_script = _load_version_module()
    (tmp_path / "webui").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b7"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b7"\n', encoding="utf-8"
    )
    (tmp_path / "webui/package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-b7"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("Version v3.3.0b7\n", encoding="utf-8")
    originals = {
        path: path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    def destructive_uv_lock(*args, **kwargs):
        (tmp_path / "uv.lock").write_text("rewritten\n", encoding="utf-8")
        (tmp_path / "README.md").unlink()
        (tmp_path / "generated-by-uv.txt").write_text("new\n", encoding="utf-8")
        return type("Result", (), {"returncode": 9})()

    monkeypatch.setattr(version_script.subprocess, "run", destructive_uv_lock)

    with pytest.raises(RuntimeError, match="uv lock failed"):
        version_script.set_version(tmp_path, "3.3.0b8")

    restored = {
        path: path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert restored == originals


@pytest.mark.parametrize("manifest", ["[]\n", '"not-an-object"\n', "null\n"])
def test_non_object_package_manifest_has_a_clear_error(tmp_path, manifest):
    version_script = _load_version_module()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "frontend/package.json").write_text(manifest, encoding="utf-8")

    errors = version_script.check_versions(tmp_path)

    assert errors == ["frontend/package.json: package manifest must be a JSON object"]
