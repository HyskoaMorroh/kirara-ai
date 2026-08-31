"""Contracts for the repository-wide version management command."""

import importlib.util
import json
import re
import subprocess
import threading
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_SCRIPT = PROJECT_ROOT / "scripts" / "version.py"


def _load_version_module():
    assert VERSION_SCRIPT.is_file(), "scripts/version.py must be the single version entry point"
    spec = importlib.util.spec_from_file_location("kirara_version_script", VERSION_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *arguments: str) -> str:
    """Run a small, isolated Git command for release identity fixtures."""
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _make_tagged_git_repo(tmp_path: Path, *, annotated: bool = False) -> tuple[Path, str, str]:
    """Create a minimal project whose only release carrier is pyproject.toml."""
    root = tmp_path / "release-repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n',
        encoding="utf-8",
    )
    _git(root, "init")
    _git(root, "config", "user.email", "version-tests@example.invalid")
    _git(root, "config", "user.name", "Version tests")
    _git(root, "add", "pyproject.toml")
    _git(root, "commit", "-m", "initial release fixture")
    commit = _git(root, "rev-parse", "HEAD")
    tag = "v3.3.0b8"
    if annotated:
        _git(root, "tag", "-a", tag, "-m", "release fixture")
    else:
        _git(root, "tag", tag)
    return root, tag, commit


def test_local_work_directory_is_excluded_from_source_docker_and_version_scans():
    version_script = _load_version_module()
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(
        encoding="utf-8"
    ).splitlines()

    assert "/work/" in gitignore
    assert "work" in dockerignore
    assert "work" in version_script.SKIP_SCAN_DIRECTORIES


@pytest.mark.parametrize(
    ("python_version", "npm_version"),
    [
        ("3.3.0", "3.3.0"),
        ("3.3.0a7", "3.3.0-a7"),
        ("3.3.0b8", "3.3.0-b8"),
        ("3.3.0rc1", "3.3.0-rc1"),
    ],
)
def test_pep440_release_versions_are_converted_to_npm_semver(
    python_version, npm_version
):
    version_script = _load_version_module()

    assert version_script.to_npm_version(python_version) == npm_version


@pytest.mark.parametrize("invalid_version", ["", "v3.3.0b8", "3.3", "not-a-version"])
def test_invalid_or_ambiguous_project_versions_are_rejected(invalid_version):
    version_script = _load_version_module()

    with pytest.raises(ValueError):
        version_script.to_npm_version(invalid_version)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("3.3.0", (3, 3, 0, None, 0)),
        ("3.3.0a7", (3, 3, 0, "a", 7)),
        ("3.3.0b8", (3, 3, 0, "b", 8)),
        ("3.3.0rc1", (3, 3, 0, "rc", 1)),
    ],
)
def test_parse_version_exposes_a_comparable_release_shape(version, expected):
    version_script = _load_version_module()

    parsed = version_script.parse_version(version)

    assert tuple(parsed) == expected


def test_compare_version_orders_release_channels_before_stable():
    version_script = _load_version_module()

    assert version_script.compare_versions("3.3.0a2", "3.3.0b1") < 0
    assert version_script.compare_versions("3.3.0b8", "3.3.0rc1") < 0
    assert version_script.compare_versions("3.3.0rc1", "3.3.0") < 0
    assert version_script.compare_versions("3.3.0", "3.3.1a1") < 0


@pytest.mark.parametrize(
    ("current", "kind", "expected"),
    [
        ("3.3.0a7", None, "3.3.0a8"),
        ("3.3.0b10", None, "3.3.0b11"),
        ("3.3.0rc1", None, "3.3.0rc2"),
        ("3.3.0b10", "rc", "3.3.0rc1"),
        ("3.3.0b10", "stable", "3.3.0"),
        ("3.3.0", "beta", "3.3.1b1"),
        ("3.3.0b10", "patch", "3.3.1"),
        ("3.3.0b10", "minor", "3.4.0"),
        ("3.3.0b10", "major", "4.0.0"),
        # `alpha` 与它的别名 `a` 都在 RELEASE_KINDS 里，此前从未被参数化覆盖，
        # 于是「alpha 迁移」这一条需求要求的转换是唯一没有测试的通道。
        # alpha 早于 beta，所以从 b10 起要另开一条 patch 线，不能回退到 3.3.0a1。
        ("3.3.0", "alpha", "3.3.1a1"),
        ("3.3.0", "a", "3.3.1a1"),
        ("3.3.0a7", "alpha", "3.3.0a8"),
        ("3.3.0a7", "beta", "3.3.0b1"),
        ("3.3.0a7", "rc", "3.3.0rc1"),
        ("3.3.0a7", "stable", "3.3.0"),
    ],
)
def test_next_version_uses_an_explicit_monotonic_release_policy(
    current, kind, expected
):
    version_script = _load_version_module()

    assert version_script.next_version(current, kind=kind) == expected


def test_alpha_never_moves_backwards_from_a_later_channel():
    """已经进到 beta/rc 之后，请求 alpha 不得把版本号退回同一条线的 alpha。

    渠道顺序是 a < b < rc < stable。若在 3.3.0b10 上请求 alpha 却得到 3.3.0a1，
    那是一个比当前版本更早的号——发布单调性被破坏，且该 tag 很可能已被占用。
    正确行为是另开下一条 patch 线。
    """
    version_script = _load_version_module()

    for current in ("3.3.0b10", "3.3.0rc2"):
        result = version_script.next_version(current, kind="alpha")

        assert version_script.compare_versions(result, current) > 0, (
            f"{current} → alpha 得到 {result}，不高于当前版本"
        )
        assert result == "3.3.1a1"


def test_next_version_skips_occupied_local_or_remote_tags():
    version_script = _load_version_module()

    assert version_script.next_version(
        "3.3.0b10", occupied_versions={"3.3.0b11", "v3.3.0b12"}
    ) == "3.3.0b13"


def test_next_version_stays_above_a_higher_published_release():
    version_script = _load_version_module()

    assert version_script.next_version(
        "3.3.0b10", occupied_versions={"v3.3.0"}
    ) == "3.3.1b1"


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("stable", "4.2.2"),
        ("patch", "4.2.2"),
        ("minor", "4.3.0"),
        ("major", "5.0.0"),
    ],
)
def test_explicit_release_kind_keeps_its_semantics_after_a_higher_tag(
    kind, expected
):
    version_script = _load_version_module()

    assert version_script.next_version(
        "3.3.0b10", kind=kind, occupied_versions={"4.2.1"}
    ) == expected


@pytest.mark.parametrize(
    ("kind", "occupied", "expected"),
    [
        ("stable", {"4.2.2"}, "4.2.3"),
        ("minor", {"4.3.0"}, "4.4.0"),
        ("major", {"5.0.0"}, "6.0.0"),
    ],
)
def test_explicit_release_kind_keeps_its_semantics_when_candidate_is_occupied(
    kind, occupied, expected
):
    version_script = _load_version_module()

    assert version_script.next_version(
        "3.3.0b10", kind=kind, occupied_versions=occupied
    ) == expected


def test_prerelease_kind_advances_after_a_higher_stable_tag_on_a_new_patch_line():
    version_script = _load_version_module()

    assert version_script.next_version(
        "3.3.0b10", kind="rc", occupied_versions={"4.2.1"}
    ) == "4.2.2rc1"


@pytest.mark.parametrize(
    ("current", "kind", "occupied", "expected"),
    [
        ("3.3.0b10", None, {"3.3.0b12"}, "3.3.0b13"),
        ("3.3.0b10", "rc", {"3.3.0"}, "3.3.1rc1"),
        ("3.3.0b10", "stable", {"3.3.0"}, "3.3.1"),
        ("3.3.0b10", None, {"3.3.1a8"}, "3.3.1b1"),
        ("3.3.0rc2", None, {"3.3.1b4"}, "3.3.1rc1"),
    ],
)
def test_next_version_stays_above_the_entire_published_timeline(
    current, kind, occupied, expected
):
    """A stale checkout must not create a release lower than an existing tag."""
    version_script = _load_version_module()

    candidate = version_script.next_version(
        current, kind=kind, occupied_versions=occupied
    )

    assert candidate == expected
    assert all(version_script.compare_versions(candidate, tag) > 0 for tag in occupied)


@pytest.mark.parametrize(
    ("current", "kind", "occupied", "expected"),
    [
        # 同渠道、同发布线：应当接着往下发，而不是整条 patch 线作废。
        ("3.3.0b10", None, {"3.3.1b4"}, "3.3.1b5"),
        ("3.3.0b10", "beta", {"3.3.1b4"}, "3.3.1b5"),
        ("3.3.0b10", "rc", {"3.3.1rc2"}, "3.3.1rc3"),
        ("3.3.0a3", "alpha", {"3.3.1a9"}, "3.3.1a10"),
    ],
)
def test_next_version_continues_an_in_flight_prerelease_line(
    current, kind, occupied, expected
):
    """已开的预发布线要接着发，不能跳到下一条 patch 线。

    别人（或另一台机器）在 `3.3.1b4` 上开了 beta 线，本机还停在 `3.3.0b10`。
    此前的结果是 `3.3.2b1`：**整条 3.3.1 线被跳过**，而 `3.3.1b5` 明明空着
    且高于全部已发布 tag。后果是版本号随机器新旧程度乱跳，
    `3.3.1` 这条线永远发不出正式版——需求 23.2 的「自动跳过冲突版本」
    指的是跳过被占用的号，不是跳过一整条线。
    """
    version_script = _load_version_module()

    candidate = version_script.next_version(
        current, kind=kind, occupied_versions=occupied
    )

    assert candidate == expected
    assert all(version_script.compare_versions(candidate, tag) > 0 for tag in occupied)


def test_stable_release_can_close_a_prerelease_line_opened_elsewhere():
    """`3.4.0b1` 已发布时，请求 stable 应当得到 `3.4.0`，而不是 `3.4.1`。

    `3.4.0` 高于 `3.4.0b1`（预发布小于同号正式版）且未被占用，正是这条
    beta 线的收尾版本。此前返回 `3.4.1`，等于「无法为别人开的预发布线发布
    正式版」，而且和 `--kind minor` 的行为自相矛盾——同一处判断，
    minor 会给出 `3.4.0`，stable 却跳到 `3.4.1`。
    """
    version_script = _load_version_module()

    assert version_script.next_version(
        "3.3.0b10", kind="stable", occupied_versions={"3.4.0b1"}
    ) == "3.4.0"


def test_stable_still_advances_when_that_stable_is_already_taken():
    """对照组：`3.4.0` 本身已被占用时才递进到 `3.4.1`。"""
    version_script = _load_version_module()

    assert version_script.next_version(
        "3.3.0b10", kind="stable", occupied_versions={"3.4.0b1", "3.4.0"}
    ) == "3.4.1"


def test_remote_git_tags_parse_ls_remote_output(monkeypatch, tmp_path):
    version_script = _load_version_module()

    def fake_git_output(root, *arguments):
        assert root == tmp_path
        assert arguments == ("ls-remote", "--tags", "--refs", "origin")
        return (
            "abc\trefs/tags/v3.3.0b10\n"
            "def\trefs/tags/v3.3.0b10^{ }\n"
            "ghi\trefs/heads/main\n"
            "jkl refs/tags/v3.3.0b11\n"
        )

    monkeypatch.setattr(version_script, "_git_output", fake_git_output)

    assert version_script._remote_git_tags(tmp_path, "origin") == {"v3.3.0b10"}


def _occupancy(version_script, versions, *, released=None):
    """Build an `OccupancyReport` for tests that only care about the collision set.

    发布计划现在读的是 `occupied_release_versions()`——它一次拿到「占用全集」
    与「为什么被占用」（远端已发布 vs 仅本地打过标，需求 23.2）。
    只关心碰撞判定的用例用这个助手构造报告，默认把全部版本记为「已发布」，
    因为那是这些用例原本模拟的情形（远端已有该 Tag）。
    """
    taken = set(versions)
    published = taken if released is None else set(released)
    return version_script.OccupancyReport(
        all_versions=version_script._sorted_versions(taken),
        released=version_script._sorted_versions(published),
        reserved_locally=version_script._sorted_versions(taken - published),
    )


def test_occupied_git_versions_normalizes_local_and_remote_tags(
    monkeypatch, tmp_path
):
    version_script = _load_version_module()
    monkeypatch.setattr(
        version_script, "_local_git_tags", lambda root: {"v3.3.0a7", "not-a-release"}
    )
    monkeypatch.setattr(
        version_script,
        "_remote_git_tags",
        lambda root, remote: {"3.3.0b10", "v3.3.0rc1"},
    )

    assert version_script.occupied_git_versions(tmp_path, remote="origin") == {
        "3.3.0a7",
        "3.3.0b10",
        "3.3.0rc1",
    }


def test_default_remote_uses_the_current_branch_upstream(monkeypatch, tmp_path):
    version_script = _load_version_module()

    def fake_git_output(root, *arguments):
        assert root == tmp_path
        if arguments == (
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ):
            return "fork/release\n"
        raise AssertionError(arguments)

    monkeypatch.setattr(version_script, "_git_output", fake_git_output)

    assert version_script.resolve_release_remote(tmp_path) == "fork"


def test_default_remote_prefers_origin_when_no_upstream_exists(monkeypatch, tmp_path):
    version_script = _load_version_module()

    def fake_try_git_output(root, *arguments):
        assert root == tmp_path
        if arguments[0] == "rev-parse":
            return None
        if arguments == ("remote",):
            return "backup\norigin\n"
        raise AssertionError(arguments)

    monkeypatch.setattr(version_script, "_try_git_output", fake_try_git_output)

    assert version_script.resolve_release_remote(tmp_path) == "origin"


def test_default_remote_requires_an_explicit_local_only_choice(monkeypatch, tmp_path):
    version_script = _load_version_module()
    monkeypatch.setattr(version_script, "_try_git_output", lambda root, *args: "")

    with pytest.raises(RuntimeError, match="--local-only"):
        version_script.resolve_release_remote(tmp_path)


def test_default_remote_rejects_a_malformed_upstream_ref(monkeypatch, tmp_path):
    version_script = _load_version_module()

    def fake_try_git_output(root, *arguments):
        assert root == tmp_path
        if arguments[0] == "rev-parse":
            return "origin"
        raise AssertionError(arguments)

    monkeypatch.setattr(version_script, "_try_git_output", fake_try_git_output)

    with pytest.raises(RuntimeError, match="malformed ref"):
        version_script.resolve_release_remote(tmp_path)


def test_explicit_local_only_never_queries_remote_tags(monkeypatch, tmp_path):
    version_script = _load_version_module()
    monkeypatch.setattr(
        version_script,
        "_local_git_tags",
        lambda root: {"v3.3.0b10"},
    )
    monkeypatch.setattr(
        version_script,
        "_remote_git_tags",
        lambda root, remote: pytest.fail("local-only must not query a remote"),
    )

    assert version_script.occupied_git_versions(tmp_path, local_only=True) == {
        "3.3.0b10"
    }


def test_remote_lookup_failure_is_not_silently_downgraded(monkeypatch, tmp_path):
    version_script = _load_version_module()
    monkeypatch.setattr(version_script, "_local_git_tags", lambda root: set())
    monkeypatch.setattr(
        version_script,
        "_remote_git_tags",
        lambda root, remote: (_ for _ in ()).throw(
            RuntimeError("git ls-remote failed: network unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="network unavailable"):
        version_script.occupied_git_versions(tmp_path, remote="origin")


@pytest.mark.parametrize("annotated", [False, True])
def test_verify_tag_accepts_lightweight_and_annotated_release_tags(tmp_path, annotated):
    version_script = _load_version_module()
    root, tag, commit = _make_tagged_git_repo(tmp_path, annotated=annotated)

    identity = version_script.verify_tag_identity(
        root, tag, expected_commit=commit, expect_head=True, local_only=True
    )

    assert identity["tag"] == tag
    assert identity["commit"] == commit
    assert identity["head"] == commit
    assert identity["head_matches"] is True
    assert identity["object_type"] == ("tag" if annotated else "commit")
    assert identity["remote"] is None
    assert identity["remote_matches"] is None


def test_verify_tag_rejects_missing_or_mismatched_release_identity(tmp_path):
    version_script = _load_version_module()
    root, tag, commit = _make_tagged_git_repo(tmp_path)

    with pytest.raises(RuntimeError, match="does not match pyproject.toml"):
        version_script.verify_tag_identity(root, "v3.3.0b7", local_only=True)

    (root / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b9"\n',
        encoding="utf-8",
    )
    with pytest.raises(version_script.GitCommandError, match="rev-parse --verify"):
        version_script.verify_tag_identity(root, "v3.3.0b9", local_only=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n',
        encoding="utf-8",
    )

    (root / "after-tag.txt").write_text("after tag\n", encoding="utf-8")
    _git(root, "add", "after-tag.txt")
    _git(root, "commit", "-m", "advance branch after release tag")
    head = _git(root, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="does not match tag"):
        version_script.verify_tag_identity(root, tag, expect_head=True, local_only=True)
    with pytest.raises(RuntimeError, match="expected commit"):
        version_script.verify_tag_identity(
            root, tag, expected_commit=head, local_only=True
        )

    assert version_script.verify_tag_identity(
        root, tag, expected_commit=commit, local_only=True
    )["head_matches"] is False


def test_verify_tag_rejects_a_remote_tag_that_points_to_another_commit(tmp_path):
    version_script = _load_version_module()
    root, tag, local_commit = _make_tagged_git_repo(tmp_path)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "origin", f"{local_commit}:refs/heads/main", tag)

    (root / "remote-only.txt").write_text("remote identity\n", encoding="utf-8")
    _git(root, "add", "remote-only.txt")
    _git(root, "commit", "-m", "create competing remote commit")
    remote_commit = _git(root, "rev-parse", "HEAD")
    _git(root, "push", "origin", f"{remote_commit}:refs/heads/main")
    _git(remote, "update-ref", f"refs/tags/{tag}", remote_commit)

    with pytest.raises(RuntimeError, match="does not match the local release identity"):
        version_script.verify_tag_identity(root, tag, remote="origin")


def test_verify_tag_json_is_stable_and_rejects_argument_injection(tmp_path, monkeypatch, capsys):
    version_script = _load_version_module()
    root, tag, commit = _make_tagged_git_repo(tmp_path)
    monkeypatch.setattr(version_script, "PROJECT_ROOT", root)

    assert version_script.main(
        ["verify-tag", "--tag", tag, "--local-only", "--json"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload) == sorted(payload)
    assert payload["commit"] == commit
    assert payload["expected_commit"] is None

    with pytest.raises(ValueError, match="invalid Git tag name"):
        version_script.verify_tag_identity(root, "--upload-pack=evil", local_only=True)
    with pytest.raises(ValueError, match="invalid Git commit name"):
        version_script.verify_tag_identity(
            root, tag, expected_commit="--upload-pack=evil", local_only=True
        )


def test_next_version_rejects_unknown_release_kind():
    version_script = _load_version_module()

    with pytest.raises(ValueError, match="unsupported release kind"):
        version_script.next_version("3.3.0b10", kind="experimental")


def test_bump_dry_run_only_resolves_a_candidate_and_does_not_write(tmp_path, monkeypatch):
    version_script = _load_version_module()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b7"\n',
        encoding="utf-8",
    )
    original = (tmp_path / "pyproject.toml").read_bytes()

    def fake_run(command, **kwargs):
        assert command[:3] == ["git", "tag", "--list"]
        return type("Result", (), {"returncode": 0, "stdout": "v3.3.0b7\n"})()

    monkeypatch.setattr(version_script.subprocess, "run", fake_run)

    assert version_script.bump_version(tmp_path, dry_run=True, local_only=True) == "3.3.0b8"
    assert (tmp_path / "pyproject.toml").read_bytes() == original


def test_bump_rejects_dirty_worktree_without_explicit_override(tmp_path, monkeypatch):
    version_script = _load_version_module()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b7"\n',
        encoding="utf-8",
    )

    def fake_run(command, **kwargs):
        if command[:3] == ["git", "tag", "--list"]:
            return type("Result", (), {"returncode": 0, "stdout": ""})()
        if command[:3] == ["git", "status", "--porcelain"]:
            return type("Result", (), {"returncode": 0, "stdout": " M notes.md\n"})()
        raise AssertionError(command)

    monkeypatch.setattr(version_script.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="working tree is dirty"):
        version_script.bump_version(tmp_path, local_only=True)


def test_bump_allow_dirty_resolves_and_applies_the_candidate(tmp_path, monkeypatch):
    version_script = _load_version_module()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b7"\n',
        encoding="utf-8",
    )
    applied = []
    monkeypatch.setattr(
        version_script,
        "occupied_release_versions",
        lambda root, remote=None, local_only=False: _occupancy(version_script, set()),
    )
    monkeypatch.setattr(
        version_script,
        "set_version",
        lambda root, version: applied.append((root, version)),
    )

    assert version_script.bump_version(
        tmp_path, allow_dirty=True, local_only=True
    ) == "3.3.0b8"
    assert applied == [(tmp_path, "3.3.0b8")]


def test_git_failures_include_the_command_and_stderr(monkeypatch, tmp_path):
    version_script = _load_version_module()

    class Failed:
        returncode = 128
        stdout = ""
        stderr = "fatal: 'missing' is not a git repository"

    monkeypatch.setattr(version_script.subprocess, "run", lambda *args, **kwargs: Failed())

    with pytest.raises(RuntimeError, match=r"git tag --list failed.*missing"):
        version_script._git_output(tmp_path, "tag", "--list")


def test_git_timeout_is_reported_as_a_release_error(monkeypatch, tmp_path):
    version_script = _load_version_module()

    def timeout(*args, **kwargs):
        raise version_script.subprocess.TimeoutExpired(args[0], 30)

    monkeypatch.setattr(version_script.subprocess, "run", timeout)

    with pytest.raises(RuntimeError, match="timed out after 30 seconds"):
        version_script._git_output(tmp_path, "tag", "--list")


def test_remote_and_local_only_are_mutually_exclusive(tmp_path):
    version_script = _load_version_module()

    with pytest.raises(ValueError, match="cannot be used together"):
        version_script.occupied_git_versions(
            tmp_path, remote="origin", local_only=True
        )


def test_cli_next_uses_the_project_root_and_prints_the_candidate(monkeypatch, capsys):
    version_script = _load_version_module()
    monkeypatch.setattr(version_script, "PROJECT_ROOT", Path("C:/repo"))
    monkeypatch.setattr(version_script, "_read_project_version", lambda root: "3.3.0b10")
    monkeypatch.setattr(
        version_script,
        "occupied_release_versions",
        lambda root, remote=None, local_only=False: _occupancy(
            version_script, {"3.3.0b10"}
        ),
    )

    assert version_script.main(["next", "--remote", "origin"]) == 0
    assert capsys.readouterr().out == "3.3.0b11\n"


def test_release_plan_derives_every_publish_identity_from_one_candidate(
    monkeypatch, tmp_path
):
    version_script = _load_version_module()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b10"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        version_script,
        "occupied_release_versions",
        lambda root, remote=None, local_only=False: _occupancy(
            version_script, {"3.3.0b9", "3.3.0b11"}
        ),
    )

    plan = version_script.build_release_plan(tmp_path, remote="origin")

    assert plan.current == "3.3.0b10"
    assert plan.candidate == "3.3.0b12"
    assert plan.npm == "3.3.0-b12"
    assert plan.tag == "v3.3.0b12"
    assert plan.kind == "auto"
    assert plan.remote == "origin"
    assert plan.local_only is False
    assert plan.occupied == ("3.3.0b9", "3.3.0b11")


def test_cli_release_plan_json_is_stable_and_machine_readable(monkeypatch, capsys):
    version_script = _load_version_module()
    monkeypatch.setattr(version_script, "PROJECT_ROOT", Path("C:/repo"))
    monkeypatch.setattr(version_script, "_read_project_version", lambda root: "3.3.0b10")
    monkeypatch.setattr(
        version_script,
        "build_release_plan",
        lambda root, kind=None, remote=None, local_only=False: version_script.ReleasePlan(
            current="3.3.0b10",
            candidate="3.3.0b11",
            npm="3.3.0-b11",
            tag="v3.3.0b11",
            kind="auto",
            remote="origin",
            local_only=False,
            occupied=("3.3.0b10",),
            # 「已发布」与「仅本地占号」分列（需求 23.2）：后者往往是上一次发布
            # 中断留下的残留，处置是删掉那个本地 Tag 再重试，而不是把版本号
            # 一路往上跳。这里模拟「远端已有 3.3.0b10」这个最常见的情形。
            released=("3.3.0b10",),
            reserved_locally=(),
        ),
    )

    assert version_script.main(["plan", "--remote", "origin", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "candidate": "3.3.0b11",
        "current": "3.3.0b10",
        "kind": "auto",
        "local_only": False,
        "npm": "3.3.0-b11",
        "occupied": ["3.3.0b10"],
        "released": ["3.3.0b10"],
        "reserved_locally": [],
        "remote": "origin",
        "tag": "v3.3.0b11",
    }


def test_bump_rechecks_remote_tags_before_synchronizing(monkeypatch, tmp_path):
    version_script = _load_version_module()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b10"\n',
        encoding="utf-8",
    )
    occupied_calls = []

    def occupied(root, remote=None, local_only=False):
        occupied_calls.append((remote, local_only))
        return set() if len(occupied_calls) == 1 else {"3.3.0b11"}

    monkeypatch.setattr(version_script, "occupied_git_versions", occupied)
    monkeypatch.setattr(
        version_script,
        "occupied_release_versions",
        lambda root, remote=None, local_only=False: _occupancy(
            version_script, occupied(root, remote, local_only)
        ),
    )
    monkeypatch.setattr(
        version_script,
        "set_version",
        lambda root, version: pytest.fail("occupied candidate must not be synchronized"),
    )

    with pytest.raises(RuntimeError, match="became occupied"):
        version_script.bump_version(
            tmp_path,
            remote="origin",
            allow_dirty=True,
        )

    assert occupied_calls == [("origin", False), ("origin", False)]


def test_bump_rejects_a_newer_different_remote_tag_before_synchronizing(
    monkeypatch, tmp_path
):
    version_script = _load_version_module()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b10"\n',
        encoding="utf-8",
    )
    occupied_calls = []

    def occupied(root, remote=None, local_only=False):
        occupied_calls.append((remote, local_only))
        return set() if len(occupied_calls) == 1 else {"3.3.0"}

    monkeypatch.setattr(version_script, "occupied_git_versions", occupied)
    monkeypatch.setattr(
        version_script,
        "occupied_release_versions",
        lambda root, remote=None, local_only=False: _occupancy(
            version_script, occupied(root, remote, local_only)
        ),
    )
    monkeypatch.setattr(
        version_script,
        "set_version",
        lambda root, version: pytest.fail("stale candidate must not be synchronized"),
    )

    with pytest.raises(RuntimeError, match="is stale.*3.3.1b1"):
        version_script.bump_version(
            tmp_path,
            remote="origin",
            allow_dirty=True,
        )

    assert occupied_calls == [("origin", False), ("origin", False)]


def test_cli_bump_dry_run_does_not_call_set_version(monkeypatch, capsys):
    version_script = _load_version_module()
    monkeypatch.setattr(version_script, "PROJECT_ROOT", Path("C:/repo"))
    monkeypatch.setattr(version_script, "_read_project_version", lambda root: "3.3.0b10")
    monkeypatch.setattr(
        version_script,
        "occupied_release_versions",
        lambda root, remote=None, local_only=False: _occupancy(version_script, set()),
    )
    monkeypatch.setattr(
        version_script,
        "set_version",
        lambda root, version: pytest.fail("dry-run must not synchronize files"),
    )

    assert version_script.main(["bump", "--dry-run", "--local-only"]) == 0
    assert capsys.readouterr().out == "version candidate: 3.3.0b11\n"


def test_transaction_snapshot_excludes_user_data_and_private_files(tmp_path):
    version_script = _load_version_module()
    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "data" / "config.yaml").write_bytes(b"private\n")
    (tmp_path / "docs" / "LOGO.jpg").write_bytes(b"logo\n")
    (tmp_path / "README.md").write_bytes(b"public\n")

    snapshot = version_script._snapshot_workspace(tmp_path)

    assert "README.md" in snapshot
    assert "data/config.yaml" not in snapshot
    assert "docs/LOGO.jpg" not in snapshot


def test_recovery_rejects_snapshot_path_traversal_and_keeps_evidence(tmp_path):
    version_script = _load_version_module()
    transaction = tmp_path / version_script.VERSION_TRANSACTION_NAME
    (transaction / "snapshot").mkdir(parents=True)
    (transaction / "manifest.json").write_text(
        json.dumps({"schema": 1, "files": ["../outside.txt"]}), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="unsafe snapshot path"):
        version_script.recover_version_transaction(tmp_path)

    assert transaction.exists()


@pytest.mark.parametrize(
    "protected_path", [".env", "data/config.yaml", "docs/LOGO.jpg", "password.hash"]
)
def test_recovery_rejects_protected_paths_and_keeps_evidence(tmp_path, protected_path):
    version_script = _load_version_module()
    transaction = tmp_path / version_script.VERSION_TRANSACTION_NAME
    (transaction / "snapshot").mkdir(parents=True)
    (transaction / "manifest.json").write_text(
        json.dumps({"schema": 2, "files": [protected_path]}), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="protected snapshot path"):
        version_script.recover_version_transaction(tmp_path)

    assert transaction.exists()


def test_recovery_reports_invalid_snapshot_file_by_its_actual_path(tmp_path):
    version_script = _load_version_module()
    transaction = tmp_path / version_script.VERSION_TRANSACTION_NAME
    (transaction / "snapshot").mkdir(parents=True)
    (transaction / "manifest.json").write_text(
        json.dumps({"schema": 2, "files": ["README.md"]}), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="invalid snapshot file 'README.md'"):
        version_script.recover_version_transaction(tmp_path)

    assert transaction.exists()


def test_snapshot_read_failure_aborts_instead_of_silently_skipping(tmp_path, monkeypatch):
    version_script = _load_version_module()
    target = tmp_path / "README.md"
    target.write_bytes(b"before\n")
    original_read_bytes = Path.read_bytes

    def fail_for_target(path):
        if path == target:
            raise OSError("temporarily unavailable")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_for_target)

    with pytest.raises(
        RuntimeError, match="cannot snapshot synchronization target 'README.md'"
    ):
        version_script._snapshot_workspace(tmp_path, [target])


def test_version_sync_lock_rejects_a_second_holder(tmp_path):
    version_script = _load_version_module()
    result = []

    with version_script._version_sync_lock(tmp_path):
        def try_lock():
            try:
                with version_script._version_sync_lock(tmp_path):
                    result.append("acquired")
            except RuntimeError as error:
                result.append(str(error))

        thread = threading.Thread(target=try_lock)
        thread.start()
        thread.join()

    assert len(result) == 1
    assert "another version synchronization" in result[0]


def test_read_only_version_checks_cannot_enter_during_a_sync(tmp_path):
    version_script = _load_version_module()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n',
        encoding="utf-8",
    )
    entered = threading.Event()
    result = []

    with version_script._version_sync_lock(tmp_path):
        entered.set()

        def try_check():
            try:
                version_script.check_versions(tmp_path)
            except RuntimeError as error:
                result.append(str(error))

        thread = threading.Thread(target=try_check)
        thread.start()
        thread.join()

    assert entered.is_set()
    assert len(result) == 1
    assert "another version synchronization" in result[0]


def test_recovery_restores_a_valid_snapshot_and_removes_transaction(tmp_path):
    version_script = _load_version_module()
    transaction = tmp_path / version_script.VERSION_TRANSACTION_NAME
    snapshot_file = transaction / "snapshot" / "README.md"
    snapshot_file.parent.mkdir(parents=True)
    snapshot_file.write_bytes(b"before\n")
    (transaction / "manifest.json").write_text(
        json.dumps({"schema": 1, "files": ["README.md"]}), encoding="utf-8"
    )
    (tmp_path / "README.md").write_bytes(b"partial\n")

    version_script.recover_version_transaction(tmp_path)

    assert (tmp_path / "README.md").read_bytes() == b"before\n"
    assert not transaction.exists()


def test_recovery_does_not_delete_private_files_created_after_snapshot(tmp_path):
    version_script = _load_version_module()
    transaction = tmp_path / version_script.VERSION_TRANSACTION_NAME
    snapshot_file = transaction / "snapshot" / "README.md"
    snapshot_file.parent.mkdir(parents=True)
    snapshot_file.write_bytes(b"before\n")
    (transaction / "manifest.json").write_text(
        json.dumps({"schema": 1, "files": ["README.md"]}), encoding="utf-8"
    )
    (tmp_path / "README.md").write_bytes(b"partial\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "new-secret.txt").write_bytes(b"keep\n")

    version_script.recover_version_transaction(tmp_path)

    assert (tmp_path / "data" / "new-secret.txt").read_bytes() == b"keep\n"


def test_set_version_updates_every_generated_version_file_and_refreshes_uv_lock(
    tmp_path, monkeypatch
):
    version_script = _load_version_module()
    monkeypatch.setattr(version_script, "_uv_command", lambda: ["uv"])
    (tmp_path / "webui").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b7"\n',
        encoding="utf-8",
    )
    (tmp_path / "webui" / "package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-b7"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b7"\n',
        encoding="utf-8",
    )
    lock_calls = []

    def fake_run(command, **kwargs):
        lock_calls.append((command, kwargs))
        pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        project_version = re.search(r'version = "([^"]+)"', pyproject).group(1)
        (tmp_path / "uv.lock").write_text(
            f'[[package]]\nname = "kirara-ai"\nversion = "{project_version}"\n',
            encoding="utf-8",
        )

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(version_script.subprocess, "run", fake_run)

    version_script.set_version(tmp_path, "3.3.0b8")

    assert 'version = "3.3.0b8"' in (
        tmp_path / "pyproject.toml"
    ).read_text(encoding="utf-8")
    package = json.loads((tmp_path / "webui" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == "3.3.0-b8"
    assert 'version = "3.3.0b8"' in (tmp_path / "uv.lock").read_text(
        encoding="utf-8"
    )
    assert lock_calls
    assert lock_calls[0][0][-1] == "lock"
    assert lock_calls[0][1]["cwd"] == tmp_path
    assert version_script.check_versions(tmp_path) == []


def test_version_artifacts_are_discovered_by_project_package_name(tmp_path):
    version_script = _load_version_module()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "node_modules" / "ignored").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n',
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "package.json").write_text(
        json.dumps({"name": "@kirara/kirara-ai-console", "version": "3.3.0-b8"}),
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "@kirara/kirara-ai-console",
                "version": "3.3.0-b8",
                "packages": {
                    "": {"version": "3.3.0-b8"},
                    "node_modules/unrelated": {"version": "3.3.0"},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "node_modules" / "ignored" / "package.json").write_text(
        json.dumps({"name": "kirara-ai-ignored", "version": "0.0.0"}),
        encoding="utf-8",
    )

    artifacts = {
        path.relative_to(tmp_path).as_posix()
        for path in version_script.discover_version_artifacts(tmp_path)
    }

    assert artifacts == {
        "pyproject.toml",
        "uv.lock",
        "frontend/package.json",
        "frontend/package-lock.json",
    }
    assert version_script.check_versions(tmp_path) == []


def test_unique_python_version_source_is_discovered_only_once(tmp_path):
    version_script = _load_version_module()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n',
        encoding="utf-8",
    )

    records = version_script.discover_version_artifact_records(tmp_path)
    pyproject_records = [
        record
        for record in records
        if record.path == tmp_path / "pyproject.toml"
    ]

    assert pyproject_records == [
        version_script.VersionArtifact(
            tmp_path / "pyproject.toml", "python-project", True
        )
    ]


def test_verify_release_candidate_accepts_a_git_tag_spelling(
    monkeypatch, tmp_path
):
    version_script = _load_version_module()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b10"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        version_script,
        "occupied_git_versions",
        lambda root, remote=None, local_only=False: set(),
    )

    version_script.verify_release_candidate(tmp_path, "v3.3.0b11")


def test_verify_release_candidate_rejects_an_invalid_candidate(tmp_path):
    version_script = _load_version_module()

    with pytest.raises(ValueError, match="unsupported release candidate"):
        version_script.verify_release_candidate(tmp_path, "next-release")


def test_manifest_discovery_uses_the_dynamic_python_project_name(tmp_path):
    version_script = _load_version_module()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "research.agent_core"\nversion = "4.2.0rc3"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "research-agent-core"\nversion = "4.2.0rc3"\n',
        encoding="utf-8",
    )
    (tmp_path / "frontend/package.json").write_text(
        json.dumps({"name": "@lab/research-agent-core-console", "version": "4.2.0-rc3"}),
        encoding="utf-8",
    )

    artifacts = {
        path.relative_to(tmp_path).as_posix()
        for path in version_script.discover_version_artifacts(tmp_path)
    }

    assert artifacts == {
        "pyproject.toml",
        "uv.lock",
        "frontend/package.json",
    }
    assert version_script.check_versions(tmp_path) == []


def test_check_reports_each_stale_generated_version(tmp_path):
    version_script = _load_version_module()
    (tmp_path / "webui").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n',
        encoding="utf-8",
    )
    (tmp_path / "webui" / "package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-b7"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b6"\n',
        encoding="utf-8",
    )

    errors = version_script.check_versions(tmp_path)

    assert any("webui/package.json" in error for error in errors)
    assert any("uv.lock" in error for error in errors)


def test_set_scans_active_text_carriers_and_preserves_history(tmp_path, monkeypatch):
    version_script = _load_version_module()
    monkeypatch.setattr(version_script, "_uv_command", lambda: ["uv"])
    for directory in ("webui", ".github/workflows", "webui/src", "tests", "build"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0a7"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0a7"\n', encoding="utf-8"
    )
    (tmp_path / "webui/package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-a7"}),
        encoding="utf-8",
    )
    carriers = {
        "README.md": "Install v3.3.0a7\n",
        ".github/workflows/release.yml": "release: 3.3.0a7\n",
        "webui/src/version.ts": "export const version = '3.3.0-a7'\n",
    }
    for relative, contents in carriers.items():
        (tmp_path / relative).write_text(contents, encoding="utf-8")
    ignored = {
        "CHANGELOG.md": "Historical v3.3.0a7\n",
        "tests/fixture.md": "Fixture 3.3.0a7\n",
        "build/generated.txt": "Generated 3.3.0a7\n",
    }
    for relative, contents in ignored.items():
        (tmp_path / relative).write_text(contents, encoding="utf-8")

    def fake_run(command, **kwargs):
        (tmp_path / "uv.lock").write_text(
            '[[package]]\nname = "kirara-ai"\nversion = "3.4.0b1"\n',
            encoding="utf-8",
        )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(version_script.subprocess, "run", fake_run)
    version_script.set_version(tmp_path, "3.4.0b1")

    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "Install v3.4.0b1\n"
    assert (tmp_path / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    ) == "release: 3.4.0b1\n"
    assert (tmp_path / "webui/src/version.ts").read_text(
        encoding="utf-8"
    ) == "export const version = '3.4.0-b1'\n"
    for relative, contents in ignored.items():
        assert (tmp_path / relative).read_text(encoding="utf-8") == contents
    assert version_script.check_versions(tmp_path) == []


def test_check_reports_stale_versions_in_active_text_but_ignores_generated_paths(
    tmp_path,
):
    version_script = _load_version_module()
    (tmp_path / "webui").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "webui/package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-b8"}),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("Still v3.3.0b7\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("History v3.3.0b7\n", encoding="utf-8")
    (tmp_path / "tests/fixture.md").write_text("Fixture v3.3.0b7\n", encoding="utf-8")
    (tmp_path / "logs/run.txt").write_text("Log v3.3.0b7\n", encoding="utf-8")

    errors = version_script.check_versions(tmp_path)

    assert len(errors) == 1
    assert "README.md" in errors[0]


def test_artifact_index_catches_stale_carrier_after_release_base_changes(
    tmp_path, monkeypatch
):
    version_script = _load_version_module()
    monkeypatch.setattr(version_script, "_uv_command", lambda: ["uv"])
    (tmp_path / "webui").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "webui/package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-b8"}),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("Install v3.3.0b8\n", encoding="utf-8")

    monkeypatch.setattr(
        version_script.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0})(),
    )
    version_script.set_version(tmp_path, "3.3.0b8")

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.4.0"\n', encoding="utf-8"
    )
    errors = version_script.check_versions(tmp_path)

    assert any("README.md: stale indexed release version(s)" in error for error in errors)


def test_artifact_index_catches_new_current_version_carrier(tmp_path, monkeypatch):
    version_script = _load_version_module()
    monkeypatch.setattr(version_script, "_uv_command", lambda: ["uv"])
    (tmp_path / "webui").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "webui/package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-b8"}),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("Install v3.3.0b8\n", encoding="utf-8")
    monkeypatch.setattr(
        version_script.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0})(),
    )
    version_script.set_version(tmp_path, "3.3.0b8")

    (tmp_path / "ops").mkdir()
    (tmp_path / "ops/release.yml").write_text("release: 3.3.0b8\n", encoding="utf-8")

    errors = version_script.check_versions(tmp_path)

    assert errors == [
        ".version-artifacts.json: active artifact is not indexed: ops/release.yml",
    ]


def test_ip_literals_and_four_segment_numbers_are_not_release_tokens(tmp_path, monkeypatch):
    """需求 23.1：artifact index 只应记录真正的发布版本号。

    索引对每个载体存一份「文中出现过的发布 token」，用来发现漂移。
    但 token 正则此前会把 `127.0.0.1` 的前三段吃成一个 `127.0.0`——
    `docs/EXTENDING.md` 里十几处 curl 示例于是贡献了 8 个假 token。

    后果不是误报，而是**掩盖**：一个载体的 token 列表里混进一堆固定噪声后，
    真正的版本号漂移在人眼和 diff 里都不再显眼。四段数字（IP、`1.2.3.4`）
    永远不是发布版本号，正则不应该在它们中间截断。
    """
    version_script = _load_version_module()

    tokens = version_script._release_tokens(
        "curl http://127.0.0.1:8080/api\n"
        "bind 0.0.0.0:8080 and 192.168.1.1\n"
        "semver 1.2.3.4 is not a release\n"
        "Install v3.3.0b11 or 3.3.0-b11 or 3.4.0\n"
    )

    assert "127.0.0" not in tokens
    assert "0.0.0" not in tokens
    assert "192.168.1" not in tokens
    assert "1.2.3" not in tokens
    # 真正的版本号三种拼法都必须仍被识别，否则漂移检测会整体失效。
    assert set(tokens) == {"v3.3.0b11", "3.3.0-b11", "3.4.0"}


def test_the_committed_artifact_index_has_no_ip_literal_tokens():
    """当前仓库的索引里不得残留 IP 字面量当成的假 token。"""
    index_path = PROJECT_ROOT / ".version-artifacts.json"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    offenders = {
        artifact["path"]: [
            token for token in artifact.get("tokens", []) if token.startswith("127.0.0")
        ]
        for artifact in data["artifacts"]
        if any(
            token.startswith("127.0.0") for token in artifact.get("tokens", [])
        )
    }
    assert not offenders, offenders


def test_discover_records_active_and_excluded_carriers_with_reasons(tmp_path):
    version_script = _load_version_module()
    (tmp_path / "docs/superpowers/plans").mkdir(parents=True)
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "ops").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text("Historical v3.3.0b7\n", encoding="utf-8")
    for planning_file in ("findings.md", "task_plan.md", "progress.md"):
        (tmp_path / planning_file).write_text(
            "Generated planning notes for v3.3.0a7\n", encoding="utf-8"
        )
    (tmp_path / "docs/superpowers/plans/release.md").write_text(
        "Old plan 3.3.0b7\n", encoding="utf-8"
    )
    (tmp_path / "docs/UPGRADING_TO_9.9.9rc4.md").write_text(
        "Historical v3.3.0b7\n", encoding="utf-8"
    )
    (tmp_path / "ops/release.yml").write_text("release: 3.3.0b7\n", encoding="utf-8")

    records = {
        record.path.relative_to(tmp_path).as_posix(): record
        for record in version_script.discover_version_artifact_records(tmp_path)
    }

    assert records["ops/release.yml"].active is True
    assert records["ops/release.yml"].carrier_type == "source-or-config"
    assert records["CHANGELOG.md"].active is False
    assert records["CHANGELOG.md"].exclusion_reason == "historical changelog"
    for planning_file in ("findings.md", "task_plan.md", "progress.md"):
        assert records[planning_file].active is False
        assert (
            records[planning_file].exclusion_reason
            == "planning and generated task material"
        )
    plan = records["docs/superpowers/plans/release.md"]
    assert plan.active is False
    assert plan.exclusion_reason == "planning and generated task material"
    historical = records["docs/UPGRADING_TO_9.9.9rc4.md"]
    assert historical.active is False
    assert historical.exclusion_reason == "historical upgrade guide"
    assert version_script.check_versions(tmp_path) == [
        "ops/release.yml: stale release version(s) [('3.3.0b7', '3.3.0b8')]"
    ]


def test_set_rolls_back_version_targets_but_preserves_unrelated_files_when_uv_lock_fails(
    tmp_path, monkeypatch
):
    version_script = _load_version_module()
    monkeypatch.setattr(version_script, "_uv_command", lambda: ["uv"])
    (tmp_path / "webui").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b7"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b7"\n', encoding="utf-8"
    )
    (tmp_path / "webui/package.json").write_text(
        json.dumps({"name": "kirara-ai-webui", "version": "3.3.0-b7"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("Version v3.3.0b7\n", encoding="utf-8")
    originals = {
        path: path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    def destructive_uv_lock(*args, **kwargs):
        (tmp_path / "uv.lock").write_text("rewritten\n", encoding="utf-8")
        (tmp_path / "README.md").unlink()
        (tmp_path / "generated-by-uv.txt").write_text("new\n", encoding="utf-8")
        return type("Result", (), {"returncode": 9})()

    monkeypatch.setattr(version_script.subprocess, "run", destructive_uv_lock)

    with pytest.raises(RuntimeError, match="uv lock failed"):
        version_script.set_version(tmp_path, "3.3.0b8")

    restored = {
        path: path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and path.name != version_script.VERSION_LOCK_NAME
    }
    expected = dict(originals)
    expected[tmp_path / "generated-by-uv.txt"] = b"new\r\n"
    assert restored == expected


def test_set_removes_a_new_version_target_but_preserves_other_new_files_on_failure(
    tmp_path, monkeypatch
):
    version_script = _load_version_module()
    monkeypatch.setattr(version_script, "_uv_command", lambda: ["uv"])
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b7"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b7"\n',
        encoding="utf-8",
    )

    def destructive_uv_lock(*args, **kwargs):
        (tmp_path / "uv.lock").write_text("rewritten\n", encoding="utf-8")
        (tmp_path / ".version-artifacts.json").write_text(
            "created by sync\n", encoding="utf-8"
        )
        (tmp_path / "unrelated.txt").write_text("keep\n", encoding="utf-8")
        return type("Result", (), {"returncode": 9})()

    monkeypatch.setattr(version_script.subprocess, "run", destructive_uv_lock)

    with pytest.raises(RuntimeError, match="uv lock failed"):
        version_script.set_version(tmp_path, "3.3.0b8")

    assert not (tmp_path / ".version-artifacts.json").exists()
    assert (tmp_path / "unrelated.txt").read_text(encoding="utf-8") == "keep\n"


def test_explicit_remote_name_rejects_option_like_values(tmp_path):
    version_script = _load_version_module()

    with pytest.raises(ValueError, match="invalid Git remote name"):
        version_script.occupied_git_versions(tmp_path, remote="--upload-pack=evil")


def test_upstream_probe_only_falls_back_when_git_reports_no_upstream(
    monkeypatch, tmp_path
):
    version_script = _load_version_module()
    calls = []

    class Failed:
        returncode = 128
        stdout = ""
        stderr = "fatal: unable to access 'https://example.invalid/repo.git': Could not resolve host"

    def fake_run(command, **kwargs):
        calls.append(command)
        return Failed()

    monkeypatch.setattr(version_script.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="unable to access"):
        version_script.resolve_release_remote(tmp_path)

    assert len(calls) == 1


def test_upstream_probe_falls_back_to_remotes_when_no_upstream_is_configured(
    monkeypatch, tmp_path
):
    version_script = _load_version_module()

    def fake_git_output(root, *arguments):
        if arguments[0] == "rev-parse":
            raise version_script.GitCommandError(
                arguments, 128, "fatal: no upstream configured for branch 'main'"
            )
        if arguments == ("remote",):
            return "origin\n"
        raise AssertionError(arguments)

    monkeypatch.setattr(version_script, "_git_output", fake_git_output)

    assert version_script.resolve_release_remote(tmp_path) == "origin"


@pytest.mark.parametrize("manifest", ["[]\n", '"not-an-object"\n', "null\n"])
def test_non_object_package_manifest_has_a_clear_error(tmp_path, manifest):
    version_script = _load_version_module()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kirara-ai"\nversion = "3.3.0b8"\n', encoding="utf-8"
    )
    (tmp_path / "frontend/package.json").write_text(manifest, encoding="utf-8")

    errors = version_script.check_versions(tmp_path)

    assert errors == ["frontend/package.json: package manifest must be a JSON object"]
