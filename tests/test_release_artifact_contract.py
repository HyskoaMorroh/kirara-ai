"""Release metadata and distribution-content contracts."""

import json
import os
import re
import tarfile
import zipfile
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.3.0a7"
WEBUI_PACKAGE_VERSION = "3.3.0-a7"

REQUIRED_DISTRIBUTION_PATHS = (
    "kirara_ai/backup/service.py",
    "kirara_ai/alembic/env.py",
    "kirara_ai/plugins/im_qqbot_adapter/assets/qqbot.png",
    "kirara_ai/plugins/im_telegram_adapter/assets/telegram.png",
    "kirara_ai/plugins/im_wecom_adapter/assets/wecom.png",
    "kirara_ai/workflow/presets/chat/plain_text.yaml",
    "kirara_ai/workflow/presets/chat/mcp_tools.yaml",
    "kirara_ai/workflow/presets/catalog.json",
)

FORBIDDEN_DISTRIBUTION_PARTS = (
    "__pycache__",
    ".pyc",
    ".pyo",
    ".env",
    ".venv",
    "docs/logo.jpg",
)


def _project_metadata() -> dict:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)


def _locked_project_version() -> str:
    lock = (PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8")
    match = re.search(
        r'\[\[package\]\]\s+name = "kirara-ai"\s+version = "([^"]+)"',
        lock,
        re.MULTILINE,
    )
    assert match is not None, "uv.lock does not contain the kirara-ai package"
    return match.group(1)


def test_release_versions_are_synchronized():
    python_version = _project_metadata()["project"]["version"]
    webui_package = json.loads(
        (PROJECT_ROOT / "webui" / "package.json").read_text(encoding="utf-8")
    )

    assert python_version == PYTHON_VERSION
    assert _locked_project_version() == python_version
    assert webui_package["version"] == WEBUI_PACKAGE_VERSION
    assert (PROJECT_ROOT / "webui" / "yarn.lock").is_file()


def test_catalog_is_declared_as_installed_package_data():
    package_data = _project_metadata()["tool"]["setuptools"]["package-data"]
    assert "catalog.json" in package_data["kirara_ai.workflow.presets"]


def _archive_members(archive: Path) -> set[str]:
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as file:
            return {name.replace("\\", "/") for name in file.namelist()}
    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, mode="r:gz") as file:
            return {name.replace("\\", "/") for name in file.getnames()}
    raise AssertionError(f"Unsupported distribution archive: {archive.name}")


def assert_distribution_contents(archive: Path) -> None:
    """Require runtime assets and reject local/generated files in wheel or sdist."""
    members = _archive_members(archive)
    normalized = {name.lower() for name in members}

    for required in REQUIRED_DISTRIBUTION_PATHS:
        assert any(name.endswith(required.lower()) for name in normalized), (
            f"{archive.name} is missing {required}"
        )

    forbidden = [
        name
        for name in normalized
        if any(part in name for part in FORBIDDEN_DISTRIBUTION_PARTS)
        or re.search(r"(^|/)data/", name)
    ]
    assert not forbidden, f"{archive.name} contains forbidden files: {forbidden[:10]}"


def test_built_distributions_when_archive_paths_are_supplied():
    """CI may pass archives through KIRARA_RELEASE_ARCHIVES after `uv build`."""
    raw_paths = os.environ.get("KIRARA_RELEASE_ARCHIVES", "")
    archives = [Path(path) for path in filter(None, raw_paths.split(os.pathsep))]
    if not archives:
        pytest.skip("KIRARA_RELEASE_ARCHIVES is not set; build artifacts first")

    wheels = [archive for archive in archives if archive.suffix == ".whl"]
    sdists = [archive for archive in archives if archive.name.endswith(".tar.gz")]
    assert len(archives) == 2, "release preflight must inspect exactly one wheel and one sdist"
    assert len(wheels) == 1, "release preflight must inspect exactly one wheel"
    assert len(sdists) == 1, "release preflight must inspect exactly one sdist"

    for archive in archives:
        assert archive.is_file(), f"release archive does not exist: {archive}"
        assert_distribution_contents(archive)
