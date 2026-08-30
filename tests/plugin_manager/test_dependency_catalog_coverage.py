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


@pytest.mark.parametrize("dependency_id", ["context-mode-plugin", "caveman-plugin"])
def test_a_claude_plugin_is_probe_and_guidance_only(
    service: SystemDependencyService, dependency_id: str
):
    """A Claude Code plugin installs into the operator's own config, not the server."""
    entry = catalog(service)[dependency_id]

    assert entry["install_supported"] is False
    assert entry["operator_guidance"]
    assert "claude" in entry["operator_guidance"].lower()


@pytest.mark.parametrize("dependency_id", ["context-mode-plugin", "caveman-plugin"])
def test_installing_a_guidance_only_dependency_is_refused(
    service: SystemDependencyService, dependency_id: str
):
    with pytest.raises(DependencyInstallUnsupported):
        service.install(dependency_id, confirmed=True, start=False)


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
