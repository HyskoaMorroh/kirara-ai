"""Regression checks for release workflows that publish project deliverables."""

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# `uv sync --frozen` 只允许从这些主机取包：pypi.org 是索引本身，
# files.pythonhosted.org 是它派发的实际下载域。国内镜像域名在境外
# runner 上不稳定，一旦进锁文件就是全线 CI 红。
UV_ALLOWED_LOCK_HOSTS = frozenset({"pypi.org", "files.pythonhosted.org"})


def _gitignore_entries() -> list[str]:
    """Return the non-comment, non-empty patterns declared in .gitignore."""
    raw = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    entries = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.append(stripped)
    return entries


def test_windows_release_upload_has_contents_write_permission():
    """The release asset step needs a token permitted to write repository contents."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "quickstart-windows.yml").read_text(
        encoding="utf-8"
    )

    assert "permissions:\n      contents: write" in workflow
    assert "svenstaro/upload-release-action@v2" in workflow


def test_webui_uses_a_typescript_version_compatible_with_its_vue_types():
    """Vue 3.5 declarations require TypeScript 5.1+ for accessor type support."""
    package = json.loads((PROJECT_ROOT / "webui" / "package.json").read_text(encoding="utf-8"))

    assert package["devDependencies"]["typescript"] == "~5.2.2"


def test_windows_quickstart_builds_the_same_bundled_webui_as_the_docker_image():
    """Quickstart bundles must not silently diverge from the image's WebUI."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "quickstart-windows.yml").read_text(
        encoding="utf-8"
    )

    # 断言「用了官方 setup-node」而不是某个具体大版本：这条契约要守的是
    # 「快速启动包与镜像用同一套受版本控制的前端源码构建」，而 action 的大版本
    # 升级（v4 → v6，node20 → node24 运行时）与这个目标无关，钉死版本只会让
    # 例行的 action 升级把这条测试变成噪音。
    assert "actions/setup-node@v" in workflow
    assert "yarn install --frozen-lockfile" in workflow
    assert "yarn build" in workflow
    assert 'Copy-Item -Path "webui/dist/*"' in workflow
    assert "DarkSkyTeam/chatgpt-for-bot-webui/releases" not in workflow

    # 快速启动包过去只跑 `yarn build`，类型错误与单测回归会被直接打进用户下载
    # 的压缩包。发布产物必须与 PR 门禁跑同一组前端检查。
    assert "yarn type-check" in workflow
    assert "yarn test:unit" in workflow


def test_windows_quickstart_publishes_only_for_the_latest_formal_release():
    """Pre-releases and non-Latest releases must not spend runner time on Windows publishing."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "quickstart-windows.yml").read_text(
        encoding="utf-8"
    )
    triggers = workflow.split("concurrency:", maxsplit=1)[0]

    assert "workflow_dispatch:" in triggers
    assert "release:\n    types:\n      - published" in triggers
    assert "push:" not in triggers
    assert "Verify latest release eligibility" in workflow
    assert 'RELEASE_IS_PRERELEASE: ${{ github.event.release.prerelease }}' in workflow
    assert 'gh api "repos/${GITHUB_REPOSITORY}/releases/latest" --jq \'.tag_name\'' in workflow
    assert "needs.release.outputs.publish == 'true'" in workflow
    assert "ref: ${{ github.event.release.tag_name || github.sha }}" in workflow


def test_prereleases_publish_a_versioned_image_without_replacing_latest():
    """Alpha releases need a testable image while stable users keep their latest tag."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml").read_text(
        encoding="utf-8"
    )

    assert "Published prerelease $RELEASE_TAG will publish only its versioned Docker image." in workflow
    assert 'if [ "${{ steps.release.outputs.publish_latest }}" = "true" ]; then' in workflow


def test_non_latest_releases_still_publish_their_versioned_image():
    """Release authors choose latest independently from availability of versioned images."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml").read_text(
        encoding="utf-8"
    )

    assert "Published release $RELEASE_TAG will publish only its versioned Docker image." in workflow


def test_docker_image_name_accepts_repository_secret_or_variable():
    """Existing repositories may keep the non-sensitive image name in either setting."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml").read_text(
        encoding="utf-8"
    )

    assert "IMAGE_NAME_VARIABLE: ${{ vars.DOCKERHUB_IMAGE }}" in workflow
    assert "IMAGE_NAME_SECRET: ${{ secrets.DOCKERHUB_IMAGE }}" in workflow
    assert 'IMAGE_NAME="${IMAGE_NAME_VARIABLE:-$IMAGE_NAME_SECRET}"' in workflow


def test_docker_workflows_share_a_cross_release_registry_cache():
    """Version tags must reuse the same Buildx cache instead of rebuilding from scratch."""
    for filename in ("docker-latest.yml", "docker-tag.yml"):
        workflow = (PROJECT_ROOT / ".github" / "workflows" / filename).read_text(
            encoding="utf-8"
        )

        assert "cache-from: type=registry,ref=${{ steps.image.outputs.name }}:buildcache" in workflow
        assert (
            "cache-to: type=registry,ref=${{ steps.image.outputs.name }}:buildcache,"
            "mode=max,image-manifest=true,oci-mediatypes=true" in workflow
        )


def test_release_builds_inject_their_version_into_the_bundled_webui():
    """Published artifacts must expose the release tag instead of an unknown UI version."""
    docker_release_workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml"
    ).read_text(encoding="utf-8")
    docker_tag_workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "docker-tag.yml"
    ).read_text(encoding="utf-8")
    windows_workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "quickstart-windows.yml"
    ).read_text(encoding="utf-8")

    assert "VITE_APP_VERSION=${{ github.event.release.tag_name" in docker_release_workflow
    assert "VITE_APP_VERSION=${{ steps.vars.outputs.tag }}" in docker_tag_workflow
    assert "VITE_APP_VERSION: ${{ github.event.release.tag_name" in windows_workflow


def test_release_preflight_checks_contracts_and_the_versioned_webui_build():
    """A quick check must catch front-end regressions before a costly release build starts."""
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "release-preflight.yml"
    ).read_text(encoding="utf-8")

    assert "branches: [main, master]" in workflow
    assert "python -m pytest tests/test_release_workflow_contract.py tests/test_webui_build_contract.py -q" in workflow
    assert "yarn type-check" in workflow
    assert "VITE_APP_VERSION: v0.0.0-ci" in workflow
    assert "yarn build" in workflow
    # vitest 与 vue-tsc 都必须是门禁的一部分，否则前端回归只能靠人工发现
    assert "yarn test:unit" in workflow


def test_the_backend_suite_gates_every_pull_request():
    """394 个后端用例只花约 50s；让它们只能手动触发等于没有后端门禁。"""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "run-tests.yml").read_text(
        encoding="utf-8"
    )
    triggers = workflow.split("concurrency:", maxsplit=1)[0]

    # 曾经这个工作流只有 workflow_dispatch，全量后端用例从不在 PR 上自动运行。
    assert "pull_request:" in triggers
    assert "push:" in triggers
    assert "merge_group:" in triggers

    # 依赖安装必须走 uv.lock：CI 与开发者本机装出不同的依赖树是最难查的一类
    # CI 假绿/假红。
    assert "uv sync --frozen" in workflow
    assert "python -m pytest ./tests -q" in workflow

    # 被后续 push 取代的 PR 运行要取消，默认分支与合并队列的结论要各自保留。
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow


def test_publishing_a_docker_image_requires_a_green_test_run():
    """镜像 push 到 Docker Hub 后无法收回，因此发布前必须重跑全量用例。"""
    for filename in ("docker-latest.yml", "docker-tag.yml"):
        workflow = (PROJECT_ROOT / ".github" / "workflows" / filename).read_text(
            encoding="utf-8"
        )

        assert "uv sync --frozen" in workflow, filename
        assert "python -m pytest ./tests -q" in workflow, filename
        # 构建/推送作业必须依赖验证作业，否则测试红了镜像照样发出去
        assert "needs: verify" in workflow, filename


def test_the_uv_lockfile_is_committed_instead_of_being_gitignored():
    """`uv sync --frozen` 在 CI 上没有锁文件就直接退出 2，本地有文件不算数。"""
    # 真实故障：uv.lock 存在于本机，却被 .gitignore 挡住从未推到 GitHub，
    # 三个跑 `uv sync --frozen` 的工作流全部失败：
    #   error: Unable to find lockfile at `uv.lock`, but `--frozen` was provided.
    # 因此这条契约同时守两件事：文件在磁盘上，且没有任何一条 .gitignore
    # 规则会把它排除掉。不调 git（CI 的 checkout 里 git 行为未必一致）。
    assert (PROJECT_ROOT / "uv.lock").is_file(), "uv.lock 缺失：`uv sync --frozen` 会直接失败"

    ignored_by = [entry for entry in _gitignore_entries() if entry.lstrip("/") in {"uv.lock", "*.lock"}]
    assert not ignored_by, (
        "uv.lock 被 .gitignore 排除（命中规则："
        + ", ".join(ignored_by)
        + "），锁文件不会随仓库分发，CI 上 `uv sync --frozen` 必然失败"
    )


def test_the_uv_lockfile_only_downloads_from_reachable_hosts():
    """境外 runner 拉不到国内镜像；锁文件里的每个下载地址都要在白名单内。"""
    # 与 tests/test_webui_build_contract.py 里的 yarn.lock 白名单同构：
    # 黑名单只能挡住已知镜像，所以逐条解析 url = "..." 并校验主机名。
    lockfile = (PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8")

    lock_hosts: dict[str, list[str]] = {}
    for raw_url in re.findall(r'url\s*=\s*"([^"]+)"', lockfile):
        without_scheme = re.sub(r"^[A-Za-z0-9+.\-]+://", "", raw_url)
        host = without_scheme.split("/")[0].split("@")[-1].split(":")[0].lower()
        lock_hosts.setdefault(host, []).append(raw_url)

    assert lock_hosts, "uv.lock 未解析出任何下载地址，解析规则可能已失效"

    unexpected = {
        host: urls[:3] for host, urls in lock_hosts.items() if host not in UV_ALLOWED_LOCK_HOSTS
    }
    assert not unexpected, (
        "uv.lock 含不可移植的下载源（仅允许 "
        + ", ".join(sorted(UV_ALLOWED_LOCK_HOSTS))
        + "）："
        + repr(unexpected)
    )


def test_the_default_uv_index_is_official_pypi_rather_than_a_mirror():
    """锁文件按 pyproject 的默认索引解析地址，索引指向镜像等于把 CI 钉死在国内网络。"""
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    index_block = pyproject.split("[[tool.uv.index]]", maxsplit=1)
    assert len(index_block) == 2, "pyproject.toml 缺少 [[tool.uv.index]]，默认索引不再受控"

    declared_url = re.search(r'^url\s*=\s*"([^"]+)"', index_block[1], re.MULTILINE)
    assert declared_url is not None, "[[tool.uv.index]] 未声明 url"
    assert declared_url.group(1) == "https://pypi.org/simple", declared_url.group(1)

    # 国内开发者用 UV_DEFAULT_INDEX 环境变量覆盖，不改这个文件；
    # 因此镜像地址只应作为注释出现，不能是生效的 url 值。
    assert 'url = "https://mirrors.ustc.edu.cn/pypi/simple"' not in pyproject


def test_every_frozen_uv_sync_workflow_pins_the_default_index():
    """即便未来某次锁文件带回镜像地址，CI 也要靠环境变量换回可达索引。"""
    workflows_dir = PROJECT_ROOT / ".github" / "workflows"
    frozen_workflows = {}
    for workflow_path in sorted(workflows_dir.glob("*.yml")):
        contents = workflow_path.read_text(encoding="utf-8")
        if "uv sync --frozen" in contents:
            frozen_workflows[workflow_path.name] = contents

    # 现状：run-tests.yml（workflow 级 env）、docker-latest.yml 与
    # docker-tag.yml（verify job 级 env）三个工作流跑 `uv sync --frozen`。
    assert set(frozen_workflows) == {
        "docker-latest.yml",
        "docker-tag.yml",
        "run-tests.yml",
    }, sorted(frozen_workflows)

    for filename, contents in frozen_workflows.items():
        assert "UV_DEFAULT_INDEX: https://pypi.org/simple" in contents, filename

