from __future__ import annotations

from typing import Any
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

from kirara_ai.web.api.system import utils


def test_packaging_is_declared_as_a_direct_runtime_dependency():
    project = tomllib.loads(
        (Path(__file__).parents[4] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert any(
        dependency.startswith("packaging>=")
        for dependency in project["project"]["dependencies"]
    )


class FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self):
        return None

    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload: dict[str, Any], requests: list[tuple[str, dict]]):
        self.payload = payload
        self.requests = requests

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def get(self, url: str, **kwargs):
        self.requests.append((url, kwargs))
        return FakeResponse(self.payload)


def _file(filename: str, **overrides: Any) -> dict[str, Any]:
    artifact = {
        "filename": filename,
        "url": f"https://packages.example/{filename}",
        "hashes": {"sha256": "test"},
        "requires-python": ">=3.10",
        "yanked": False,
    }
    artifact.update(overrides)
    return artifact


async def _lookup(monkeypatch, files: list[dict[str, Any]], registry: str):
    requests: list[tuple[str, dict]] = []
    payload = {
        "meta": {"api-version": "1.4"},
        "name": "kirara-ai",
        "files": files,
    }
    monkeypatch.setattr(
        utils.aiohttp,
        "ClientSession",
        lambda: FakeSession(payload, requests),
    )
    result = await utils.get_latest_pypi_version("kirara-ai", registry)
    return result, requests


@pytest.mark.asyncio
async def test_pypi_lookup_uses_simple_api_and_selects_newer_prerelease(monkeypatch):
    files = [
        _file("kirara_ai-3.2.0-py3-none-any.whl"),
        _file("kirara_ai-3.3.0b8-py3-none-any.whl"),
    ]

    result, requests = await _lookup(
        monkeypatch, files, "https://mirror.example/custom/simple/"
    )

    assert result == (
        "3.3.0b8",
        "https://packages.example/kirara_ai-3.3.0b8-py3-none-any.whl",
    )
    assert requests == [
        (
            "https://mirror.example/custom/simple/kirara-ai/",
            {"headers": {"Accept": "application/vnd.pypi.simple.v1+json"}},
        )
    ]


@pytest.mark.asyncio
async def test_pypi_lookup_ignores_yanked_python_and_platform_incompatible_files(
    monkeypatch,
):
    files = [
        _file("kirara_ai-9.0.0-py3-none-any.whl", yanked="broken release"),
        _file(
            "kirara_ai-8.0.0-py3-none-any.whl",
            **{"requires-python": ">=99"},
        ),
        _file("kirara_ai-7.0.0-cp27-cp27m-win32.whl"),
        _file("kirara_ai-3.3.0b8-py3-none-any.whl"),
    ]

    result, _ = await _lookup(monkeypatch, files, "https://pypi.org/simple")

    assert result[0] == "3.3.0b8"
    assert result[1].endswith("kirara_ai-3.3.0b8-py3-none-any.whl")


@pytest.mark.asyncio
async def test_pypi_lookup_prefers_wheel_for_a_release_and_falls_back_to_sdist(
    monkeypatch,
):
    wheel = _file("kirara_ai-3.3.0b8-py3-none-any.whl")
    sdist = _file("kirara_ai-3.3.0b8.tar.gz")

    result, _ = await _lookup(monkeypatch, [sdist, wheel], "https://pypi.org/simple")
    assert result == ("3.3.0b8", wheel["url"])

    result, _ = await _lookup(monkeypatch, [sdist], "https://pypi.org/simple")
    assert result == ("3.3.0b8", sdist["url"])


@pytest.mark.asyncio
async def test_pypi_lookup_returns_empty_result_when_no_installable_file(monkeypatch):
    files = [
        _file("kirara_ai-9.0.0-py3-none-any.whl", yanked=True),
        _file("not-a-python-package.txt"),
    ]

    result, _ = await _lookup(monkeypatch, files, "https://pypi.org/simple")

    assert result == ("0.0.0", "")
