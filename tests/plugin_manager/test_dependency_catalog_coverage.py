"""The dependency catalog must cover every tool 1.txt asks to be installable.

1.txt item 10 requires that the operator can install the named toolchain onto the
VPS from the front end and have it participate in the unified dependency view:
rtk, context-mode, graphify, memsearch and caveman.

Only `graphify` was catalogued. The rest had no entry at all, so the front end
could neither report whether they were present nor install them — the operator had
to SSH in, which is exactly what that requirement exists to avoid.

Two distinctions this pins:

- A tool with a real, non-interactive installer gets `install_commands`.
- A Claude Code *plugin* (context-mode, caveman) is installed into an operator's
  own Claude configuration, not into the server runtime. Fabricating an installer
  for it would run the wrong command against the wrong target, so those entries
  are probe-plus-guidance only. `install_supported` is False and the front end
  shows the guidance instead of an install button.
"""

from __future__ import annotations

import pytest

from kirara_ai.plugin_manager.system_dependencies import (
    DependencyInstallUnsupported,
    SystemDependencyService,
)


@pytest.fixture()
def service(tmp_path) -> SystemDependencyService:
    return SystemDependencyService(tmp_path)


def catalog(service: SystemDependencyService) -> dict[str, dict]:
    return {item["dependency_id"]: item for item in service.list_dependencies()}


REQUIRED_IDS = (
    "rtk-cli",
    "context-mode-plugin",
    "graphify-cli",
    "memsearch-cli",
    "caveman-plugin",
)


@pytest.mark.parametrize("dependency_id", REQUIRED_IDS)
def test_every_named_tool_is_catalogued(service: SystemDependencyService, dependency_id: str):
    assert dependency_id in catalog(service)


@pytest.mark.parametrize("dependency_id", REQUIRED_IDS)
def test_every_named_tool_can_be_probed(service: SystemDependencyService, dependency_id: str):
    entry = catalog(service)[dependency_id]

    # A catalogued tool with no probe could never report readiness.
    assert entry["status"] in {"unknown", "ready", "missing", "failed"}
    definition = service._definition(dependency_id)
    assert definition.probe_commands


def test_rtk_has_a_real_installer(service: SystemDependencyService):
    entry = catalog(service)["rtk-cli"]

    assert entry["install_supported"] is True


def test_memsearch_has_a_real_installer(service: SystemDependencyService):
    entry = catalog(service)["memsearch-cli"]

    assert entry["install_supported"] is True


def test_context_mode_is_installable_on_the_server(service: SystemDependencyService):
    """Context Mode 是 npm 上有 bin 入口的普通包，服务器侧能装、能探测。

    这一条替换的是原来那句「Claude Code 插件、服务器侧无法安装」的断言——
    那个前提是错的，实测：

        $ npm view context-mode version   -> 1.0.169
        $ npm ls -g --depth=0             -> context-mode@1.0.169
        package.json: "bin": {"context-mode": "./cli.bundle.mjs"}

    它自述支持 Claude Code / Gemini CLI / VS Code Copilot / OpenCode / Codex CLI，
    也就是说它不绑定任何一个宿主。按 `claude --version` 探测会两个方向都答错：
    装了 context-mode 但没装 Claude CLI 的 VPS 报 missing，
    装了 Claude CLI 却没装 context-mode 的机器报 ready。
    """
    entry = catalog(service)["context-mode-plugin"]

    assert entry["install_supported"] is True
    assert entry["kind"] == "cli"
    # 探测自己的可执行文件，而不是某个宿主 CLI。
    definition = service._definition("context-mode-plugin")
    assert definition.probe_commands[0][0] == "context-mode"
    assert definition.install_commands == (
        ("npm", "install", "-g", "context-mode"),
    )


def test_caveman_is_probed_but_not_installed_by_the_server(
    service: SystemDependencyService,
):
    """Caveman 有自己的可执行文件，但公共 npm 上装不到，因此只探测不代装。

    与 context-mode 的区别是**分发渠道**而不是「是不是插件」：

        $ npm view caveman-installer  -> E404 Not Found
        $ npm view caveman            -> 一个无关的 JS 模板引擎

    本机那份是 `caveman-installer@2.0.0`（`bin: caveman`）。猜一个
    `npm i -g caveman` 会装上那个模板引擎：命令存在、探测通过、
    而功能完全不是要的那个——比报 missing 更糟。
    """
    entry = catalog(service)["caveman-plugin"]

    assert entry["install_supported"] is False
    assert entry["operator_guidance"]
    # 指引必须说清「为什么装不了」，而不是笼统地推给运维：
    # 运维照着一句「请安装 caveman」会装上那个模板引擎。
    assert "npm" in entry["operator_guidance"]
    definition = service._definition("caveman-plugin")
    assert definition.probe_commands[0][0] == "caveman"


def test_installing_caveman_is_refused(service: SystemDependencyService):
    with pytest.raises(DependencyInstallUnsupported):
        service.install("caveman-plugin", confirmed=True, start=False)


def test_rtk_is_not_claimed_to_be_the_same_tool_as_the_type_kit(
    service: SystemDependencyService,
):
    """`rtk` collides with an unrelated package name; the description must disambiguate."""
    entry = catalog(service)["rtk-cli"]

    assert "token" in entry["description"].lower()


def test_the_probe_for_a_missing_tool_reports_missing_not_ready(
    service: SystemDependencyService,
):
    def always_missing(argv, **_kwargs):
        raise FileNotFoundError(argv[0])

    service._runner = always_missing  # type: ignore[method-assign]
    result = service.probe("rtk-cli")

    assert result["ready"] is False
    assert result["status"] in {"missing", "failed"}


def test_prerequisites_point_at_a_catalogued_dependency(service: SystemDependencyService):
    known = set(catalog(service))

    for dependency_id in REQUIRED_IDS:
        definition = service._definition(dependency_id)
        for prerequisite in definition.prerequisites:
            assert prerequisite in known


def test_required_by_names_the_feature_that_needs_it(service: SystemDependencyService):
    for dependency_id in REQUIRED_IDS:
        entry = catalog(service)[dependency_id]
        assert entry["required_by"], dependency_id
