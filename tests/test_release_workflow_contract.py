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
    assert "ref: ${{ needs.preflight.outputs.release_commit }}" in workflow


def test_prereleases_publish_a_versioned_image_without_replacing_latest():
    """Alpha releases need a testable image while stable users keep their latest tag."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml").read_text(
        encoding="utf-8"
    )

    assert "Published prerelease $RELEASE_TAG will publish only its versioned Docker image." in workflow
    assert 'if [ "${{ steps.release.outputs.publish_latest }}" = "true" ]; then' in workflow


def test_latest_docker_publishes_are_serialized_across_release_tags():
    """An older slow release must never overwrite a newer latest image."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml").read_text(
        encoding="utf-8"
    )

    assert "group: docker-latest-current" in workflow
    assert "group: docker-latest-${{ github.ref }}" not in workflow
    assert "cancel-in-progress: false" in workflow


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
    """Every build derives the WebUI identity from the single version command."""
    docker_release_workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml"
    ).read_text(encoding="utf-8")
    docker_tag_workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "docker-tag.yml"
    ).read_text(encoding="utf-8")
    windows_workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "quickstart-windows.yml"
    ).read_text(encoding="utf-8")

    assert "VITE_APP_VERSION=${{ steps.release.outputs.tag }}" in docker_release_workflow
    assert "VITE_APP_VERSION=${{ steps.vars.outputs.git_tag }}" in docker_tag_workflow
    assert "tags: ${{ steps.image.outputs.name }}:${{ steps.vars.outputs.image_version }}" in docker_tag_workflow
    assert "steps.vars.outputs.tag" not in docker_tag_workflow
    assert "VITE_APP_VERSION: ${{ steps.version.outputs.tag }}" in windows_workflow
    assert "python scripts/version.py tag" in windows_workflow
    assert "VITE_APP_VERSION=v3.3.0a7" not in docker_release_workflow
    assert "VITE_APP_VERSION=v3.3.0a7" not in docker_tag_workflow
    assert "VITE_APP_VERSION: v3.3.0a7" not in windows_workflow


def test_release_preflight_checks_contracts_and_the_versioned_webui_build():
    """A quick check must catch front-end regressions before a costly release build starts."""
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "release-preflight.yml"
    ).read_text(encoding="utf-8")

    assert "branches: [main, master]" in workflow
    contract_command = "\n".join(
        (
            "          python -m pytest \\",
            "            tests/test_release_artifact_contract.py \\",
            "            tests/test_release_workflow_contract.py \\",
            "            tests/test_webui_build_contract.py -q",
        )
    )
    assert contract_command in workflow
    assert "yarn type-check" in workflow
    assert "VITE_APP_VERSION: ${{ steps.version.outputs.tag }}" in workflow
    assert "python scripts/version.py tag" in workflow
    assert "yarn build" in workflow
    # vitest 与 vue-tsc 都必须是门禁的一部分，否则前端回归只能靠人工发现
    assert "yarn test:unit" in workflow
    assert '(root / "version.json").read_text' in workflow
    assert 'metadata["version"] == expected' in workflow
    assert 'metadata["packageVersion"] == package_version' in workflow


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
    """Docker publish must depend on the complete reusable release gate."""
    for filename in ("docker-latest.yml", "docker-tag.yml"):
        workflow = (PROJECT_ROOT / ".github" / "workflows" / filename).read_text(
            encoding="utf-8"
        )

        assert "uses: ./.github/workflows/release-preflight.yml" in workflow, filename
        assert "run_backend_and_image: true" in workflow, filename
        assert "needs: verify" in workflow, filename
        assert "push: true" in workflow, filename

    preflight = (PROJECT_ROOT / ".github" / "workflows" / "release-preflight.yml").read_text(
        encoding="utf-8"
    )
    assert "uses: ./.github/workflows/run-tests.yml" in preflight
    assert "secrets: inherit" in preflight
    assert "release_commit:" in preflight
    assert "value: ${{ jobs.version.outputs.release_commit }}" in preflight
    assert "release_commit: ${{ needs.version.outputs.release_commit }}" in preflight
    assert "ref: ${{ needs.version.outputs.release_commit }}" in preflight
    assert "tests/test_a4_upgrade_contract.py" in preflight
    assert "yarn type-check" in preflight
    assert "yarn test:unit" in preflight
    assert "uv build --out-dir dist-release-check" in preflight
    assert 'if path.suffix == ".whl" or path.name.endswith(".tar.gz")' in preflight
    assert "Smoke-test a fresh wheel installation" in preflight


def test_windows_release_archive_uses_exact_paths_and_excludes_private_data():
    """The final Windows ZIP gate must inspect normalized exact members."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "quickstart-windows.yml").read_text(
        encoding="utf-8"
    )

    assert "$lower -notcontains $path" in workflow
    assert "EndsWith(\"/$path\")" not in workflow
    assert "docs/logo" not in workflow.lower()
    assert "allowedTopLevel" in workflow
    assert "docs/LOGO.jpg" in (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "EXPECTED_UI_VERSION: ${{ steps.version.outputs.tag }}" in workflow
    assert 'web/version.json" -Raw' in workflow
    assert "$metadata.version -ne $env:EXPECTED_UI_VERSION" in workflow
    assert "$metadata.packageVersion -ne $expectedPackageVersion" in workflow
    assert "uses: ./.github/workflows/release-preflight.yml" in workflow
    assert "run_backend_and_image: true" in workflow
    assert "Get-ChildItem -Path \"${{ env.DIST_DIR }}\" -Recurse -Force -File" in workflow
    assert "Where-Object { $_.Name -match '\\.(pyc|pyo)$' }" in workflow
    assert "Where-Object { $_.Name -in @('__pycache__'" in workflow


def test_release_workflows_reject_unexpected_manual_version_tags():
    """Release entry points derive the accepted tag from the checked source."""
    latest = (PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml").read_text(
        encoding="utf-8"
    )
    tagged = (PROJECT_ROOT / ".github" / "workflows" / "docker-tag.yml").read_text(
        encoding="utf-8"
    )

    for workflow in (latest, tagged):
        assert "python scripts/version.py verify-tag" in workflow
        assert "v3.3.0a7" not in workflow
    assert 'RELEASE_TAG"' in latest
    assert "python scripts/version.py verify-tag \\" in latest
    assert '            --expected-commit "$source_commit" \\' in latest
    assert "            --expect-head \\" in latest
    assert '            --remote origin' in latest
    assert 'EXPECTED_TAG="$(python scripts/version.py tag)"' in latest
    assert 'git_tag="$(python scripts/version.py tag)"' in tagged
    assert 'image_version="$(python scripts/version.py get)"' in tagged
    assert '            --expected-commit "$source_commit" \\' in tagged
    assert 'echo "git_tag=$git_tag" >> "$GITHUB_OUTPUT"' in tagged
    assert 'echo "image_version=$image_version" >> "$GITHUB_OUTPUT"' in tagged
    assert "workflow_dispatch:" not in latest.split("release:", maxsplit=1)[0]


def test_every_job_that_derives_the_version_pins_its_python():
    """调用 `scripts/version.py` 的 job 必须能真的跑起来它。

    该脚本用 `tomllib` 读 `pyproject.toml`，那是 3.11 才进标准库的模块。
    不装 Python 时它落到 runner 镜像预装的解释器上：今天恰好够用，
    镜像一换成 3.10 这些门禁就会以 `ModuleNotFoundError` 失败，
    而失败信息看起来像是「版本不同步」，排查方向完全被带偏。
    需求 23.3 要求 CI/Docker 使用**同一个**发布身份——解释器版本
    也是这条身份链的一环，不能各 job 各碰运气。

    3.10 是允许的，**但必须在同一个 job 里显式安装 `tomli` 回退包**
    （`scripts/version.py` 有 `tomllib`→`tomli` 的 import 回退）。
    这条断言之前只检查「存在 setup-python 这个字符串」，所以既抓不到
    「版本被降到 3.8」也抓不到「3.10 但忘了装 tomli」。现在按 job 解析。

    同时钉住调用形式统一为 `python`（而不是混用 `python3`）：
    `actions/setup-python` 只保证 `python` 指向所选版本。
    """
    import re

    workflows_root = PROJECT_ROOT / ".github" / "workflows"
    offenders: list[str] = []
    for path in sorted(workflows_root.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if "scripts/version.py" not in text:
            continue
        if "python3 scripts/version.py" in text:
            offenders.append(f"{path.name}: 仍在用 python3，应统一为 python")

        # 按 job 切分：job 键在两级缩进（`jobs:` 下一层）。
        job_blocks: dict[str, str] = {}
        current: str | None = None
        for line in text.splitlines():
            match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if match:
                current = match.group(1)
                job_blocks[current] = ""
                continue
            if current is not None:
                job_blocks[current] += line + "\n"

        for job_name, block in job_blocks.items():
            if "scripts/version.py" not in block:
                continue
            versions = re.findall(r"python-version:\s*[\"']?([0-9.]+)[\"']?", block)
            if not versions:
                offenders.append(
                    f"{path.name}:{job_name} 调用 version.py 但没有 setup-python"
                )
                continue
            for version in versions:
                major, _, minor = version.partition(".")
                if (int(major), int(minor or 0)) >= (3, 11):
                    continue
                # 3.10 及更早需要 tomli 回退包，且必须装在同一个 job 里。
                if "tomli" not in block:
                    offenders.append(
                        f"{path.name}:{job_name} 用 Python {version} 跑 version.py"
                        " 但没有安装 tomli 回退包"
                    )
    assert not offenders, offenders


def test_every_build_and_release_entry_point_uses_the_version_command():
    """Adding a new version carrier must not silently bypass the single source."""
    workflows = PROJECT_ROOT / ".github" / "workflows"
    required = {
        "release-preflight.yml": "python scripts/version.py check",
        "run-tests.yml": "python scripts/version.py check",
        "project_check.yml": "python scripts/version.py check",
        "docker-latest.yml": "scripts/version.py verify-tag",
        "docker-tag.yml": "scripts/version.py verify-tag",
        "quickstart-windows.yml": "python scripts/version.py check",
    }
    for filename, command in required.items():
        contents = (workflows / filename).read_text(encoding="utf-8")
        assert command in contents, filename

    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY scripts/version.py ./scripts/version.py" in dockerfile
    assert "RUN python scripts/version.py check" in dockerfile
    assert "python scripts/version.py tag > /release-tag" in dockerfile
    assert "python scripts/version.py npm > /npm-version" in dockerfile


def test_manual_docker_publish_uses_the_dispatch_commit_source():
    """A rebuild of an existing tag must use the exact commit that passed verification."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docker-tag.yml").read_text(
        encoding="utf-8"
    )

    assert "name: Validate default branch source" in workflow
    assert "DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}" in workflow
    assert 'GITHUB_REF_TYPE" != "branch"' in workflow
    assert 'GITHUB_REF_NAME" != "$DEFAULT_BRANCH"' in workflow
    assert "needs: validate-source" in workflow
    assert "name: Checkout dispatch commit" in workflow
    assert "ref: ${{ needs.verify.outputs.release_commit }}" in workflow
    assert "expected_commit: ${{ github.sha }}" in workflow
    assert "Checkout tagged source" not in workflow
    assert "ref: ${{ github.ref }}" not in workflow
    assert 'GITHUB_REF_TYPE" != "tag"' not in workflow
    assert 'GITHUB_REF_NAME" != "$TAG_NAME"' not in workflow


def test_manual_docker_publish_separates_git_tag_from_image_version():
    """Git tags keep their v prefix while registry tags use the package version."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docker-tag.yml").read_text(
        encoding="utf-8"
    )

    assert "INPUT_TAG: ${{ inputs.image_tag }}" in workflow
    assert 'INPUT_TAG="${{ inputs.image_tag }}"' not in workflow
    assert 'if [ "$INPUT_TAG" != "$git_tag" ]; then' in workflow
    assert 'tags: ${{ steps.image.outputs.name }}:${{ steps.vars.outputs.image_version }}' in workflow
    assert 'VITE_APP_VERSION=${{ steps.vars.outputs.git_tag }}' in workflow
    assert 'tags: ${{ steps.image.outputs.name }}:v' not in workflow


def test_release_docker_workflow_has_no_unreachable_manual_publish_branch():
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docker-latest.yml").read_text(
        encoding="utf-8"
    )

    assert 'if [ "$GITHUB_EVENT_NAME" = "workflow_dispatch" ]; then' not in workflow


def test_release_documentation_keeps_ui_version_injection_dynamic():
    """Operational examples must not teach a fixed UI version build argument."""
    upgrade = (PROJECT_ROOT / "docs" / "UPGRADING.md").read_text(
        encoding="utf-8"
    )

    assert "VITE_APP_VERSION=v3.3.0a7" not in upgrade
    assert "scripts/version.py tag" in upgrade
    assert "scripts/version.py verify-tag" in upgrade
    assert "--expected-commit $commit" in upgrade
    assert "--expect-head" in upgrade
    assert "--remote origin" in upgrade


def test_release_documentation_uses_smart_version_resolution_without_fixed_next_version():
    """Release instructions must derive the next version and inspect tag collisions."""
    upgrade = (PROJECT_ROOT / "docs" / "UPGRADING.md").read_text(encoding="utf-8")
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    for document in (upgrade, readme):
        assert "scripts/version.py next" in document
        assert "scripts/version.py bump" in document
        assert "--remote origin" in document
        assert "--kind stable" in document
        assert "Read-Host" not in document


def test_release_documentation_does_not_freeze_the_current_version_in_its_intro():
    """The upgrade guide must remain current without a version-tool rewrite."""
    upgrade = (PROJECT_ROOT / "docs" / "UPGRADING.md").read_text(encoding="utf-8")
    introduction = upgrade.split("## 0. 版本维护规则", maxsplit=1)[0]

    assert not re.search(r"`v?\d+\.\d+\.\d+(?:a|b|rc)\d+`", introduction)
    assert "文档中的当前版本标记由" not in introduction


def test_release_documentation_matches_the_actual_docker_publish_entry_points():
    """Operators must be sent to the workflow that can really be dispatched."""
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "`Docker build latest` 仅由已发布的 GitHub Release 触发" in readme
    assert "手动运行 `Docker build with tags`" in readme
    assert "手动运行 `Docker build latest`" not in readme


def test_release_documentation_separates_git_and_registry_tags():
    """Git uses v-prefixed tags while Docker uses the package version."""
    upgrade = (PROJECT_ROOT / "docs" / "UPGRADING.md").read_text(encoding="utf-8")
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    for document in (upgrade, readme):
        assert "scripts/version.py tag" in document
        assert "scripts/version.py get" in document
    assert '$imageVersion = (& $versionTool scripts/version.py get).Trim()' in upgrade
    assert '-t "kirara-ai:$imageVersion"' in upgrade
    assert "-t kirara-ai:$releaseTag" not in upgrade


def test_workflow_call_release_gates_include_the_frozen_index_override():
    """The reusable release gate must retain the network/lock contract."""
    run_tests = (PROJECT_ROOT / ".github" / "workflows" / "run-tests.yml").read_text(
        encoding="utf-8"
    )
    preflight = (PROJECT_ROOT / ".github" / "workflows" / "release-preflight.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_call:" in run_tests
    assert "workflow_call:" in preflight
    assert "UV_DEFAULT_INDEX: https://pypi.org/simple" in run_tests


def test_release_gates_use_distinct_reusable_workflow_concurrency_groups():
    """Nested workflow calls must not cancel one another through a shared group."""
    run_tests = (PROJECT_ROOT / ".github" / "workflows" / "run-tests.yml").read_text(
        encoding="utf-8"
    )
    preflight = (PROJECT_ROOT / ".github" / "workflows" / "release-preflight.yml").read_text(
        encoding="utf-8"
    )

    assert "group: ${{ github.workflow }}-run-tests-${{ github.ref }}" in run_tests
    assert "group: ${{ github.workflow }}-release-preflight-${{ github.ref }}" in preflight


def test_release_smoke_checks_are_read_only_and_secret_safe():
    """The image gate must exercise diagnostics without calling external tools."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "run-tests.yml").read_text(
        encoding="utf-8"
    )

    assert "VITE_APP_VERSION=${{ steps.version.outputs.tag }}" in workflow
    assert "python scripts/version.py tag" in workflow
    assert 'readiness.get("ready") is True' in workflow
    assert 'isinstance(checks, list)' in workflow
    assert '"/backend-api/api/dispatch/reachability"' in workflow
    assert '"/backend-api/api/dispatch/preview"' in workflow
    assert '"/backend-api/api/mcp/tools"' in workflow
    assert '"/backend-api/api/mcp/servers/' not in workflow
    assert '"/call"' not in workflow
    assert 'assert password not in decoded' in workflow
    assert 'assert password not in repr(payload)' in workflow


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

    # 现状：run-tests.yml（workflow 级 env）是唯一直接跑 `uv sync --frozen`
    # 的工作流；Docker 发布通过 reusable release-preflight 间接调用它。
    assert set(frozen_workflows) == {
        "run-tests.yml",
    }, sorted(frozen_workflows)

    for filename, contents in frozen_workflows.items():
        assert "UV_DEFAULT_INDEX: https://pypi.org/simple" in contents, filename


def test_no_release_entry_point_treats_an_offline_candidate_as_published():
    """需求 23.2：不得把离线候选当作正式发布版本。

    `--local-only` 让版本推导**跳过远端 Tag 核验**——它存在是为了断网时也能
    在本地推一个候选出来看看。但一个没有和远端对过的候选不能拿去发布：
    远端可能已经有同名 Tag（另一台机器刚发过），此时发布会撞车或覆盖。

    `verify_tag_identity` 与 `plan_release` 都接受这个开关，因此「谁在调用它」
    是一条必须由契约钉住的边界：任何真正发布产物的 workflow 都不许传它。
    这条断言此前不存在，于是「哪天顺手加上去省掉一次网络调用」不会被任何门禁
    拦住——而那正是把离线候选发成正式版本的路径。
    """
    workflows = PROJECT_ROOT / ".github" / "workflows"
    offenders: list[str] = []
    for path in sorted(workflows.glob("*.yml")):
        contents = path.read_text(encoding="utf-8")
        if "--local-only" in contents or "local_only" in contents:
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} 在发布链路上使用了离线版本推导。"
        "`--local-only` 跳过远端 Tag 核验，只适用于本地排查，"
        "不得出现在任何构建或发布 workflow 里。"
    )


def test_pr_type_check_cannot_execute_fork_code_with_a_write_token():
    """Untrusted pull-request code must only run with a read-only token and no secrets."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "pr_review.yml").read_text(
        encoding="utf-8"
    )
    triggers = workflow.split("permissions:", maxsplit=1)[0]

    assert "pull_request:" in triggers
    assert "pull_request_target:" not in workflow
    assert "contents: read" in workflow
    assert "pull-requests: write" not in workflow
    assert "issues: write" not in workflow
    assert "actions/github-script" not in workflow
    assert "secrets.GITHUB_TOKEN" not in workflow
    assert "python -m mypy" in workflow


def test_published_images_record_the_commit_they_were_built_from():
    """镜像必须带上源提交，否则「Tag 与提交绑定」在发布之后就断了（需求 16）。

    `verify-tag` 只检查**当下**的自洽：本地 Tag、远端 Tag 与 HEAD 是否指向同一个
    提交。它没有任何历史记录，因此下面这条时间线完全通得过：

    1. 打一个版本 Tag，`release: published` 触发 `docker-latest.yml`，推出镜像；
    2. 事后把同一个 Tag 移到另一个提交（`git tag -f` + 强推）；
    3. 手动跑一次 `docker-tag.yml`，`image_tag` 填同一个 Tag。

    第 3 步的所有校验都会通过——Tag、远端与 HEAD 此刻确实一致——于是 Docker Hub 上
    同一个版本标签被换成了另一份内容，而**没有任何地方记录这件事发生过**。
    拉到旧镜像的人和拉到新镜像的人都认为自己跑的是同一个版本。

    修法不是禁止重建（重建有正当理由：基础镜像补安全更新）。而是让镜像自己带上
    它的源提交：`org.opencontainers.image.revision` 一进标签，两份镜像就能被区分，
    「这个版本标签是哪个提交构建的」从无从查证变成一条 `docker inspect`。
    """
    workflows = ("docker-latest.yml", "docker-tag.yml")
    for name in workflows:
        workflow = (PROJECT_ROOT / ".github" / "workflows" / name).read_text(
            encoding="utf-8"
        )
        assert "org.opencontainers.image.revision" in workflow, (
            f"{name} 推出的镜像没有记录源提交，移动 Tag 后重建无法与原镜像区分"
        )
        # 版本与来源也要在标签里，否则只有 revision 时仍答不出「它自称是哪个版本」。
        assert "org.opencontainers.image.version" in workflow, (
            f"{name} 的镜像标签缺少版本"
        )
        assert "org.opencontainers.image.source" in workflow, (
            f"{name} 的镜像标签缺少源仓库地址"
        )


def test_the_recorded_revision_comes_from_the_verified_commit():
    """revision 必须取自已经过 `verify-tag` 校验的那个提交。

    写 `github.sha` 看起来等价，实际上不是：手动发布路径已经显式比对过
    checkout 的提交与 preflight 的提交（`docker-tag.yml` 的 `Set output` 步骤），
    revision 若另取一个来源，就可能记下一个没被校验过的值——
    那正好抵消了这条标签的全部意义。
    """
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docker-tag.yml").read_text(
        encoding="utf-8"
    )
    revision_line = next(
        (
            line
            for line in workflow.splitlines()
            if "org.opencontainers.image.revision" in line
        ),
        None,
    )
    assert revision_line is not None
    assert "steps.vars.outputs" in revision_line or "source_commit" in revision_line, (
        "revision 没有使用 verify-tag 校验过的提交，"
        f"实际是：{revision_line.strip()}"
    )
