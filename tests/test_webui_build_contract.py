from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEBUI_ROOT = PROJECT_ROOT / "webui"


def test_bundled_webui_exposes_backup_settings_tab():
    """The Docker image must build the maintained WebUI with the backup entry point."""
    settings_view = WEBUI_ROOT / "src" / "views" / "settings" / "BasicSettings.vue"
    backup_card = WEBUI_ROOT / "src" / "views" / "settings" / "components" / "BackupCard.vue"

    assert settings_view.is_file()
    assert backup_card.is_file()

    contents = settings_view.read_text(encoding="utf-8")
    assert "import BackupCard" in contents
    assert 'name="backup"' in contents
    assert "<BackupCard" in contents


def test_dockerfile_builds_the_bundled_webui_instead_of_an_unpinned_npm_tag():
    """A reproducible image cannot rely on npm's mutable beta dist-tag."""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "AS frontend-builder" in dockerfile
    assert "COPY --from=frontend-builder /webui/dist /app/web" in dockerfile
    assert 'registry.npmjs.org/kirara-ai-webui' not in dockerfile


def test_bundled_webui_lockfile_uses_an_available_registry():
    """The vendored build must not inherit the unavailable local mirror URL."""
    lockfile = (WEBUI_ROOT / "yarn.lock").read_text(encoding="utf-8")

    assert "https://registry.npmjs.org/" in lockfile
    assert "mirrors.cloud.tencent.com" not in lockfile
