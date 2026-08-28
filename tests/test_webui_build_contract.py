import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


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


def test_frontend_builder_is_architecture_independent_and_retries_registry_downloads():
    """多架构镜像不应在 QEMU 下重复构建 WebUI 并被短暂网络抖动卡死。"""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM --platform=$BUILDPLATFORM node:20-bookworm-slim AS frontend-builder" in dockerfile
    assert "yarn install --frozen-lockfile --network-timeout 120000" in dockerfile
    assert "for attempt in 1 2 3; do" in dockerfile


def test_wheel_builder_copies_only_package_build_inputs():
    """Unrelated workflow and runtime data changes must not invalidate the wheel layer."""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY pyproject.toml README.md LICENSE MANIFEST.in uv.lock ./" in dockerfile
    assert "COPY kirara_ai ./kirara_ai" in dockerfile
    assert "COPY . ." not in dockerfile


def test_docker_runtime_dependencies_are_exported_from_the_committed_uv_lock():
    """镜像安装依赖必须复用开发与 CI 都校验过的锁文件。"""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY pyproject.toml README.md LICENSE MANIFEST.in uv.lock ./" in dockerfile
    assert "uv export --frozen --no-dev --no-emit-project" in dockerfile
    assert "RUN --mount=type=cache,target=/root/.cache/pip" in dockerfile
    assert "pip install --require-hashes" in dockerfile
    assert "--timeout 120 --retries 10 -r requirements.txt" in dockerfile
    assert "PyPI dependency download failed; retrying install" in dockerfile
    assert "for attempt in 1 2 3; do" in dockerfile
    assert "pip install --no-cache-dir --no-deps *.whl" in dockerfile


def test_webui_declares_its_yarn_runtime_and_a_non_mutating_lint_check():
    """开发者与 CI 应使用同一个 Yarn 版本，检查命令不应改写源码。"""
    package = (WEBUI_ROOT / "package.json").read_text(encoding="utf-8")

    assert '"packageManager": "yarn@1.22.22"' in package
    assert '"lint:check": "eslint src/' in package


def test_bundled_webui_lockfile_uses_an_available_registry():
    """The vendored build must not inherit the unavailable local mirror URL."""
    # 黑名单只能挡住已知镜像，npmmirror.com 就是这样漏进锁文件的；
    # 因此改为白名单：解析每一条 resolved 地址，逐个校验主机名。
    lockfile = (WEBUI_ROOT / "yarn.lock").read_text(encoding="utf-8")

    assert "https://registry.npmjs.org/" in lockfile

    # yarn install --frozen-lockfile 会直接按 resolved 地址取包，
    # 只有下列主机在任意 CI / 用户网络下都可达。
    allowed_hosts = {
        "registry.npmjs.org",
        # 少数依赖以 git / GitHub tarball 形式安装时的合法主机
        "codeload.github.com",
        "github.com",
    }

    resolved_hosts: dict[str, list[str]] = {}
    for raw_url in re.findall(r'^\s*resolved\s+"?([^"\s]+)"?\s*$', lockfile, re.MULTILINE):
        # 去掉 git+ssh:// 等前缀与 #commit / ?query 后缀，只留主机名
        without_scheme = re.sub(r"^[A-Za-z0-9+.\-]+://", "", raw_url)
        host = without_scheme.split("/")[0].split("@")[-1].split(":")[0].lower()
        resolved_hosts.setdefault(host, []).append(raw_url)

    assert resolved_hosts, "yarn.lock 未解析出任何 resolved 地址，解析规则可能已失效"

    unexpected = {
        host: urls[:3] for host, urls in resolved_hosts.items() if host not in allowed_hosts
    }
    assert not unexpected, (
        "yarn.lock 含不可移植的下载源（仅允许 "
        + ", ".join(sorted(allowed_hosts))
        + "）："
        + repr(unexpected)
    )


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
    """Every build derives its release identity from synchronized package metadata."""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    vite_config = (WEBUI_ROOT / "vite.config.ts").read_text(encoding="utf-8")
    version_utility = (WEBUI_ROOT / "src" / "utils" / "version.ts").read_text(
        encoding="utf-8"
    )

    assert "ARG VITE_APP_VERSION" in dockerfile
    assert "ENV VITE_APP_VERSION=${VITE_APP_VERSION}" in dockerfile
    assert 'expected_version="$(cat /release-tag)"' in dockerfile
    assert '"${VITE_APP_VERSION}" != "${expected_version}"' in dockerfile
    assert "const configuredVersion = process.env.VITE_APP_VERSION?.trim()" in vite_config
    assert "configuredVersion !== expectedVersion" in vite_config
    assert "return expectedVersion" in vite_config
    assert "execSync" not in vite_config
    assert "valid as semverValid" in version_utility
    assert "PEP_440_PRERELEASE" in version_utility
    assert "normalizeAppVersion" in version_utility


def test_webui_build_emits_machine_readable_version_metadata():
    """Runtime update checks need the package version even without Git metadata."""
    vite_config = (WEBUI_ROOT / "vite.config.ts").read_text(encoding="utf-8")
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "version.json" in vite_config
    assert "packageVersion: packageJson.version" in vite_config
    assert "version: appVersion" in vite_config
    assert "dist/version.json" in dockerfile


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


def test_wheel_declares_bundled_workflow_presets():
    """pip 安装包必须带上首次部署所需的进阶工作流 YAML。"""
    manifest = (PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    pyproject_text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    pyproject = tomllib.loads(pyproject_text)
    presets_root = PROJECT_ROOT / "kirara_ai" / "workflow" / "presets" / "chat"
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert "recursive-include kirara_ai/workflow/presets *" in manifest
    assert {"*/*.yaml", "catalog.json"}.issubset(
        package_data["kirara_ai.workflow.presets"]
    )
    assert {"__pycache__/*", "*.py[cod]"}.issubset(
        pyproject["tool"]["setuptools"]["exclude-package-data"]["*"]
    )
    assert {
        "dsr_thinking.yaml",
        "normal_multimodal.yaml",
        "talk_break.yaml",
        # MCP、函数调用、时间感知等新增模板同样必须随 wheel 分发
        "mcp_tools.yaml",
        "function_calling.yaml",
        "time_aware.yaml",
        "plain_text.yaml",
        "sensitive_word_filter.yaml",
        "long_reply_split.yaml",
        "custom_script.yaml",
        "group_mention.yaml",
    }.issubset({path.name for path in presets_root.glob("*.yaml")})
    # package-data 的 `*/*.yaml` 只覆盖 presets 下一层子目录。新增预设分组时
    # 必须放在这一层，否则 pip 安装的用户拿不到这些文件。
    presets_parent = presets_root.parent
    for yaml_path in presets_parent.rglob("*.yaml"):
        assert yaml_path.parent.parent == presets_parent, (
            f"{yaml_path} 的层级不被 package-data 的 */*.yaml 覆盖"
        )


def test_docker_default_data_starts_without_runtime_test_fixture():
    """新容器须初始化受版本控制的默认数据，不能携带测试工作流。"""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    start_script = (PROJECT_ROOT / "docker" / "start.sh").read_text(encoding="utf-8")
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "COPY ./data /tmp/data" not in dockerfile
    assert "COPY ./data/dispatch_rules /tmp/data/dispatch_rules" in dockerfile
    assert "COPY ./data/workflows /tmp/data/workflows" in dockerfile
    assert "COPY ./data/fonts /tmp/data/fonts" in dockerfile
    assert 'cp -r /tmp/data/. /app/data' in start_script
    assert "data/workflows/**/test-workflow-new.yaml" in dockerignore
    assert "data/web/password.hash" in dockerignore
    # 创建者身份等同于凭据：镜像里出现它就等于把服务器侧操作权限打包分发。
    assert "data/web/creator.subject" in dockerignore
    assert "data/creator.subject" in dockerignore


def test_docker_context_excludes_local_audit_and_runtime_state():
    """本地审计、浏览器状态和运行态数据不得上传到 Docker 构建上下文。"""
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    required_patterns = {
        ".qa-*",
        ".playwright-mcp",
        # 浏览器自动化与规划留痕都不属于发布内容：镜像里出现它们就是把
        # 本地探索产物打进了产品。
        ".playwright-cli",
        ".superpowers",
        "PATHFINDER-2026-08-21",
        "data/db",
        "data/mcp",
        "data/resources",
        "data/sessions",
        "data/plugins",
        "data/dispatch_rules/.transactions",
        "data/workflows/.transactions",
        "*.db",
        "*.sqlite",
        "*.sqlite3",
        "*.log",
        "*.pid",
    }

    configured_patterns = {
        line.strip().rstrip("/")
        for line in dockerignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert required_patterns.issubset(configured_patterns)
