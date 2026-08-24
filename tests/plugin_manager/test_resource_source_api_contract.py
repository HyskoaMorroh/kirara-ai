from __future__ import annotations

import pytest

from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_sources import ResourceSourceService


def test_client_cannot_override_server_generated_source_identity(tmp_path, monkeypatch):
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    source = ResourceSourceService(lifecycle)
    monkeypatch.setattr(source, "_download_bytes", lambda _url: b"not a repository archive")

    with pytest.raises(Exception):
        source.install_skill(
            owner="owner",
            name="repo",
            branch="main",
            directory="skills/demo",
            source_key="client-forged",
        )

