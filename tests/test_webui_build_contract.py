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


def test_wheel_builder_copies_only_package_build_inputs():
    """Unrelated workflow and runtime data changes must not invalidate the wheel layer."""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY pyproject.toml README.md LICENSE MANIFEST.in ./" in dockerfile
    assert "COPY kirara_ai ./kirara_ai" in dockerfile
    assert "COPY . ." not in dockerfile


def test_bundled_webui_lockfile_uses_an_available_registry():
    """The vendored build must not inherit the unavailable local mirror URL."""
    lockfile = (WEBUI_ROOT / "yarn.lock").read_text(encoding="utf-8")

    assert "https://registry.npmjs.org/" in lockfile
    assert "mirrors.cloud.tencent.com" not in lockfile


def test_websocket_client_uses_a_secure_scheme_when_the_page_uses_https():
    """Console and tracing sockets must not be blocked as mixed content on HTTPS sites."""
    http_client = (WEBUI_ROOT / "src" / "utils" / "http.ts").read_text(encoding="utf-8")

    assert "const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'" in http_client
    assert "new URL(`${wsProtocol}//${window.location.host}`)" in http_client
    assert "wsUrl.protocol =" not in http_client


def test_webui_build_uses_content_hashed_assets_for_safe_proxy_caching():
    """A release must not mix an old cached entry module with new lazy-loaded chunks."""
    vite_config = (WEBUI_ROOT / "vite.config.ts").read_text(encoding="utf-8")

    assert "entryFileNames: `assets/[name]-[hash].js`" in vite_config
    assert "chunkFileNames: `assets/[name]-[hash].js`" in vite_config
    assert "assetFileNames: `assets/[name]-[hash][extname]`" in vite_config


def test_webui_build_accepts_an_explicit_release_version_without_git_metadata():
    """Container builds must display their release tag even when .git is excluded."""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    vite_config = (WEBUI_ROOT / "vite.config.ts").read_text(encoding="utf-8")
    version_utility = (WEBUI_ROOT / "src" / "utils" / "version.ts").read_text(
        encoding="utf-8"
    )

    assert "ARG VITE_APP_VERSION" in dockerfile
    assert "ENV VITE_APP_VERSION=${VITE_APP_VERSION}" in dockerfile
    assert "const configuredVersion = process.env.VITE_APP_VERSION?.trim()" in vite_config
    assert "return configuredVersion" in vite_config
    assert "valid as semverValid" in version_utility
    assert "replace(/^(\\d+\\.\\d+\\.\\d+)a(\\d+)$/, '$1-a$2')" in version_utility


def test_non_editor_routes_do_not_eagerly_load_the_monaco_runtime():
    """The large Monaco runtime must remain lazy until an editor route needs it."""
    vite_config = (WEBUI_ROOT / "vite.config.ts").read_text(encoding="utf-8")

    assert "'vsc': [" not in vite_config


def test_configuration_save_does_not_log_password_hash_settings():
    """Saving a configuration must not expose password-related settings in browser logs."""
    configuration_list = (WEBUI_ROOT / "src" / "components" / "ConfigurationList.vue").read_text(
        encoding="utf-8"
    )

    assert "console.log(props.configurationGroups[0].properties[property].password)" not in configuration_list
