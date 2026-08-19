from unittest.mock import AsyncMock

import pytest

from kirara_ai.web import app as web_app


@pytest.mark.parametrize("metadata", [None, "not-json", "[]", "{}"])
def test_legacy_webui_install_is_refreshed_when_version_metadata_is_missing_or_invalid(
    tmp_path, monkeypatch, metadata
):
    (tmp_path / "index.html").write_text("legacy", encoding="utf-8")
    if metadata is not None:
        (tmp_path / "version.json").write_text(metadata, encoding="utf-8")
    scheduled = []

    def fake_create_task(coroutine):
        scheduled.append(coroutine)
        coroutine.close()
        return object()

    monkeypatch.setattr(web_app, "STATIC_FOLDER", str(tmp_path))
    monkeypatch.setattr(web_app.asyncio, "create_task", fake_create_task)
    server = object.__new__(web_app.WebServer)
    server._install_webui = AsyncMock()

    server._check_and_install_webui()

    assert len(scheduled) == 1
    assert hasattr(server, "_webui_install_task")


def test_current_webui_install_is_not_downloaded_again(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("current", encoding="utf-8")
    (tmp_path / "version.json").write_text(
        '{"packageVersion":"3.3.0-b8"}', encoding="utf-8"
    )
    scheduled = []
    monkeypatch.setattr(web_app, "STATIC_FOLDER", str(tmp_path))
    monkeypatch.setattr(web_app.asyncio, "create_task", scheduled.append)
    server = object.__new__(web_app.WebServer)
    server._install_webui = AsyncMock()

    server._check_and_install_webui()

    assert scheduled == []
    assert not hasattr(server, "_webui_install_task")
