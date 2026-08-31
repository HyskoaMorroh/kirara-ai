#!/usr/bin/env python3
"""Synchronize release metadata from the single version in ``pyproject.toml``.

The command deliberately discovers carriers from the repository instead of
maintaining a list of filenames.  Human release work only changes the Python
project version; package manifests, lock files, source constants, CI, Docker,
and operational documentation are derived and checked here.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, BinaryIO, Iterator, NamedTuple

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 support; tomli is a project dependency.
    import tomli as tomllib  # type: ignore[no-redef]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_INDEX_NAME = ".version-artifacts.json"
VERSION_TRANSACTION_NAME = ".version-sync-transaction"
VERSION_TRANSACTION_TEMP_NAME = ".version-sync-transaction.tmp"
VERSION_LOCK_NAME = ".version-sync.lock"
VERSION_PATTERN = re.compile(r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?")
RELEASE_KINDS = frozenset(
    {"a", "alpha", "b", "beta", "rc", "stable", "patch", "minor", "major"}
)
RELEASE_STAGE_RANK = {"a": 0, "b": 1, "rc": 2, None: 3}
ANY_RELEASE_TOKEN_PATTERN = re.compile(
    # 左界加上 `.`、右界加上 `(?!\.\d)`：四段数字永远不是发布版本号。
    # 没有这两条时 `127.0.0.1` 会被截成 `127.0.0`，于是 `docs/EXTENDING.md`
    # 里每一处 curl 示例都往 artifact index 里塞一个假 token。那不是误报
    # （检查仍然通过），而是**掩盖**：真正的版本漂移混在固定噪声里不再显眼。
    r"(?<![A-Za-z0-9.])v?(?P<release>\d+\.\d+\.\d+)"
    r"(?P<separator>-?)(?P<stage>a|b|rc|alpha|beta)?(?P<number>\d+)?"
    r"(?![A-Za-z0-9])(?!\.\d)"
)
TEXT_VERSION_SUFFIXES = frozenset(
    {
        ".cfg",
        ".cjs",
        ".ini",
        ".js",
        ".json",
        ".mjs",
        ".md",
        ".py",
        ".ps1",
        ".rst",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)
MAX_VERSION_SCAN_BYTES = 4 * 1024 * 1024

# These directories can contain enormous or machine-owned content.  Their
# contents are never release carriers and are skipped before reading files.
SKIP_SCAN_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".playwright-cli",
        ".playwright-mcp",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        ".venv-win",
        "artifacts",
        "data",
        "dist",
        "graphify-out",
        "logs",
        "node_modules",
        "work",
        VERSION_TRANSACTION_NAME,
        VERSION_TRANSACTION_TEMP_NAME,
        VERSION_LOCK_NAME,
    }
)

# These paths are intentionally visible in ``discover`` as excluded records:
# they can contain old release strings but must not be rewritten by ``set``.
EXCLUDED_DIRECTORY_REASONS = {
    "tests": "test fixtures and assertions",
    "build": "generated build output",
    ".superpowers": "planning and generated task material",
    "superpowers": "planning and generated task material",
}
EXCLUDED_FILE_REASONS = {
    "CHANGELOG.md": "historical changelog",
    "findings.md": "planning and generated task material",
    "task_plan.md": "planning and generated task material",
    "progress.md": "planning and generated task material",
    "webui/UPSTREAM.md": "upstream version record",
    "scripts/version.py": "version synchronization implementation",
    ARTIFACT_INDEX_NAME: "version synchronization index",
}
HISTORICAL_UPGRADE_GUIDE_PATTERN = re.compile(
    r"^docs/UPGRADING_TO_[^/]+\.md$", re.IGNORECASE
)
TRANSACTION_PROTECTED_FILES = frozenset(
    {
        ".env",
        "config.cfg",
        "config.json",
        "config.yaml",
        "config.yaml.bak",
        "password.hash",
        "docs/LOGO.jpg",
        "docs/logo.jpg",
    }
)
_VERSION_LOCK_STATE: dict[Path, tuple[BinaryIO, int, int]] = {}
_VERSION_LOCK_STATE_GUARD = threading.RLock()


class ArtifactIndex(NamedTuple):
    """Persisted audit data for dynamically discovered active carriers."""

    path: str
    tokens: tuple[str, ...]


class VersionArtifact(NamedTuple):
    """One discovered version carrier and its audit state."""

    path: Path
    carrier_type: str
    active: bool
    exclusion_reason: str | None = None


class ReleaseVersion(NamedTuple):
    """A supported release version in a comparison-friendly shape."""

    major: int
    minor: int
    patch: int
    stage: str | None
    number: int


class OccupancyReport(NamedTuple):
    """Which release versions are taken, and *why* each one is taken.

    需求 23.2 点名「不得把离线候选当作正式发布版本」。碰撞判定对两类占用是
    一样的（都不能重用），但处置完全不同：

    - ``released``：远端已有这个 Tag，它已经发布过，接着往下找号；
    - ``reserved_locally``：只有本地有——一次没推成功的打标。它占住了一个号，
      而**没有任何发布产物与它对应**，删掉那个本地 Tag 再重试往往才是对的。

    把两者显示成同一个词的后果是版本号被无谓地跳过：一次失败的打标之后每次
    重跑计划都会跳过那个号，几次之后版本号里出现空洞，而没有任何地方记录
    那些号去哪了。
    """

    #: 全集，与 `occupied_git_versions()` 的返回内容一致（按发布顺序排序）。
    all_versions: tuple[str, ...]
    #: 远端已有 → 已发布。
    released: tuple[str, ...]
    #: 仅本地有 → 已占号但未发布。
    reserved_locally: tuple[str, ...]


class ReleasePlan(NamedTuple):
    """One read-only release decision derived from source metadata and Git tags."""

    current: str
    candidate: str
    npm: str
    tag: str
    kind: str
    remote: str | None
    local_only: bool
    occupied: tuple[str, ...]
    #: `occupied` 里已经发布过的那部分（远端有 Tag）。
    released: tuple[str, ...] = ()
    #: `occupied` 里仅本地打过标、从未发布的那部分。
    reserved_locally: tuple[str, ...] = ()


class TagIdentity(NamedTuple):
    """The immutable Git identity represented by one release tag."""

    tag: str
    object_id: str
    object_type: str
    commit: str


class GitCommandError(RuntimeError):
    """A Git failure whose exit status can be classified by callers."""

    def __init__(self, arguments: tuple[str, ...], returncode: int, detail: str):
        command = "git " + " ".join(arguments)
        suffix = f": {detail}" if detail else ""
        super().__init__(
            f"{command} failed with exit code {returncode}{suffix}"
        )
        self.arguments = arguments
        self.returncode = returncode


def to_npm_version(project_version: str) -> str:
    """Convert the supported PEP 440 release spellings to npm semver."""
    match = VERSION_PATTERN.fullmatch(project_version)
    if match is None:
        raise ValueError(
            f"unsupported project version {project_version!r}; expected X.Y.Z, "
            "X.Y.ZaN, X.Y.ZbN, or X.Y.ZrcN"
        )
    release, prerelease, number = match.groups()
    return release if prerelease is None else f"{release}-{prerelease}{number}"


def parse_version(version: str) -> ReleaseVersion:
    """Parse one supported PEP 440 release into comparable components."""
    match = VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError(
            f"unsupported project version {version!r}; expected X.Y.Z, "
            "X.Y.ZaN, X.Y.ZbN, or X.Y.ZrcN"
        )
    release, stage, number = match.groups()
    major, minor, patch = (int(part) for part in release.split("."))
    return ReleaseVersion(major, minor, patch, stage, int(number or 0))


def _release_sort_key(version: ReleaseVersion) -> tuple[int, int, int, int, int]:
    return (
        version.major,
        version.minor,
        version.patch,
        RELEASE_STAGE_RANK[version.stage],
        version.number,
    )


def compare_versions(left: str, right: str) -> int:
    """Compare two supported release versions using release ordering."""
    left_key = _release_sort_key(parse_version(left))
    right_key = _release_sort_key(parse_version(right))
    return (left_key > right_key) - (left_key < right_key)


def _format_release(version: ReleaseVersion) -> str:
    release = f"{version.major}.{version.minor}.{version.patch}"
    return release if version.stage is None else f"{release}{version.stage}{version.number}"


def _canonical_release_kind(kind: str | None) -> str | None:
    if kind is None:
        return None
    normalized = kind.strip().lower()
    if normalized not in RELEASE_KINDS:
        choices = ", ".join(sorted(RELEASE_KINDS))
        raise ValueError(f"unsupported release kind {kind!r}; expected one of: {choices}")
    return {"alpha": "a", "beta": "b"}.get(normalized, normalized)


def _increment_patch(version: ReleaseVersion) -> ReleaseVersion:
    return ReleaseVersion(version.major, version.minor, version.patch + 1, None, 0)


def _first_candidate_after(
    highest: ReleaseVersion, stage: str | None
) -> ReleaseVersion:
    """Return the first requested prerelease that can follow ``highest``."""
    if stage is not None and highest.stage is not None:
        # A later prerelease channel can continue on the same release line:
        # 3.3.1a8 -> 3.3.1b1 and 3.3.1b4 -> 3.3.1rc1.
        if RELEASE_STAGE_RANK[stage] > RELEASE_STAGE_RANK[highest.stage]:
            return ReleaseVersion(
                highest.major, highest.minor, highest.patch, stage, 1
            )
    next_line = _increment_patch(highest)
    return next_line._replace(stage=stage, number=1 if stage is not None else 0)


def _candidate_for_kind(current: ReleaseVersion, kind: str | None) -> ReleaseVersion:
    """Apply the explicit release policy before occupied-tag collision checks."""
    if kind is None:
        if current.stage is not None:
            return current._replace(number=current.number + 1)
        return _increment_patch(current)
    if kind == "stable":
        if current.stage is not None:
            return current._replace(stage=None, number=0)
        return _increment_patch(current)
    if kind == "patch":
        return _increment_patch(current)
    if kind == "minor":
        return ReleaseVersion(current.major, current.minor + 1, 0, None, 0)
    if kind == "major":
        return ReleaseVersion(current.major + 1, 0, 0, None, 0)

    target_stage = kind
    if current.stage == target_stage:
        return current._replace(number=current.number + 1)
    if current.stage is None:
        base = _increment_patch(current)
    elif RELEASE_STAGE_RANK[target_stage] <= RELEASE_STAGE_RANK[current.stage]:
        base = _increment_patch(current)
    else:
        base = current
    return base._replace(stage=target_stage, number=1)


def _candidate_after_highest(
    candidate: ReleaseVersion,
    highest: ReleaseVersion,
    kind: str | None,
) -> ReleaseVersion:
    """Move a candidate beyond the published timeline without changing policy.

    这里只负责「越过已发布时间线」，不改变 ``_candidate_for_kind`` 决定的渠道
    语义。两条容易被跳过的情况必须显式处理，否则整条发布线会被作废：

    * **同渠道、同发布线继续往下发**：别人在 ``3.3.1b4`` 上开了 beta 线而本机
      还停在 ``3.3.0b10`` 时，``3.3.1b5`` 空着且高于全部已发布 tag，应当用它，
      而不是跳到 ``3.3.2b1``。需求要求的是「跳过被占用的号」，不是「跳过一整条线」。
    * **为别人开的预发布线收尾**：``3.4.0b1`` 已发布时请求 stable 应当得到
      ``3.4.0``（预发布小于同号正式版，且该号未被占用），而不是 ``3.4.1``；
      否则同一处判断会与 ``--kind minor`` 自相矛盾。

    调用方在 while 循环里反复调用本函数，因此「候选被占用」这一情况仍由循环
    继续推进，本函数只需保证每次返回的候选严格高于 ``highest``。
    """
    if kind == "minor":
        return ReleaseVersion(highest.major, highest.minor + 1, 0, None, 0)
    if kind == "major":
        return ReleaseVersion(highest.major + 1, 0, 0, None, 0)
    if candidate.stage is None:
        # 正式版可以给同号预发布线收尾：3.4.0b1 已发布时 3.4.0 仍然更高且空着。
        if highest.stage is not None:
            return ReleaseVersion(highest.major, highest.minor, highest.patch, None, 0)
        return _increment_patch(highest)
    if (
        candidate.major,
        candidate.minor,
        candidate.patch,
        candidate.stage,
    ) == (
        highest.major,
        highest.minor,
        highest.patch,
        highest.stage,
    ):
        return candidate._replace(number=highest.number + 1)
    # 请求的渠道与最高已发布号同渠道时，继续那一条线而不是另开 patch 线。
    if candidate.stage == highest.stage:
        return ReleaseVersion(
            highest.major,
            highest.minor,
            highest.patch,
            candidate.stage,
            highest.number + 1,
        )
    return _first_candidate_after(highest, candidate.stage)


def _normalize_candidate_version(value: str) -> str | None:
    normalized = _normalize_release_token(value.strip())
    if normalized is None:
        return None
    try:
        parse_version(normalized)
    except ValueError:
        return None
    return normalized


def next_version(
    current_version: str,
    *,
    kind: str | None = None,
    occupied_versions: set[str] | None = None,
) -> str:
    """Resolve the next non-conflicting release without changing any files.

    The default keeps alpha, beta, and rc releases on their current channel.
    A stable source advances to the next patch release.  Channel changes and
    major/minor releases are explicit so a routine bump cannot guess a risky
    release boundary.
    """
    current = parse_version(current_version)
    canonical_kind = _canonical_release_kind(kind)
    occupied = {
        normalized
        for value in (occupied_versions or set())
        if (normalized := _normalize_candidate_version(value)) is not None
    }
    candidate = _candidate_for_kind(current, canonical_kind)
    highest_occupied = max(
        (parse_version(version) for version in occupied),
        key=_release_sort_key,
        default=None,
    )
    while _format_release(candidate) in occupied or (
        highest_occupied is not None
        and _release_sort_key(candidate) <= _release_sort_key(highest_occupied)
    ):
        if highest_occupied is None:
            candidate = candidate._replace(number=candidate.number + 1)
        else:
            # A stale checkout must advance from the highest published line,
            # while explicit minor/major requests retain their requested scope.
            candidate = _candidate_after_highest(candidate, highest_occupied, canonical_kind)
    result = _format_release(candidate)
    if compare_versions(result, current_version) <= 0:
        raise RuntimeError(
            f"calculated version {result} does not advance current version {current_version}"
        )
    return result


def to_git_tag(project_version: str) -> str:
    to_npm_version(project_version)
    return f"v{project_version}"


def _read_project_metadata(root: Path) -> tuple[str, str]:
    path = root / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot parse pyproject.toml: {error}") from error
    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml has no [project] table")
    name = project.get("name")
    project_version = project.get("version")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("pyproject.toml [project] has no project name")
    if not isinstance(project_version, str):
        raise ValueError("pyproject.toml [project] has no literal version")
    to_npm_version(project_version)
    return name, project_version


def _read_project_version(root: Path) -> str:
    return _read_project_metadata(root)[1]


def _write_text(path: Path, contents: str) -> None:
    # Bytes preserve an existing CRLF/LF choice on Windows and avoid unrelated
    # line-ending churn in documentation and workflow files.
    path.write_bytes(contents.encode("utf-8"))


def _write_durable_bytes(path: Path, contents: bytes) -> None:
    """Write transaction evidence and flush it before publishing its directory."""
    with path.open("wb") as handle:
        handle.write(contents)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    """Flush a directory when the host filesystem exposes directory handles."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            # Windows commonly rejects fsync on directory handles; file-level
            # flushes still make the transaction evidence durable where possible.
            pass
    finally:
        os.close(descriptor)


def _write_project_version(root: Path, project_version: str) -> None:
    path = root / "pyproject.toml"
    contents = path.read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", contents)
    if project is None:
        raise ValueError("pyproject.toml has no [project] table")
    updated_project, replacements = re.subn(
        r'(^version\s*=\s*)"[^"]+"(\s*$)',
        rf'\1"{project_version}"\2',
        project.group(0),
        count=1,
        flags=re.MULTILINE,
    )
    if replacements != 1:
        raise ValueError("pyproject.toml [project] has no literal version")
    _write_text(path, contents[: project.start()] + updated_project + contents[project.end() :])


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _artifact_index_path(root: Path) -> Path:
    return root / ARTIFACT_INDEX_NAME


def _is_skipped(path: Path, root: Path) -> bool:
    return any(part in SKIP_SCAN_DIRECTORIES for part in path.relative_to(root).parts)


def _is_package_manifest(path: Path) -> bool:
    return path.name == "package.json"


def _is_structured_artifact(path: Path, root: Path) -> bool:
    return (
        path == root / "pyproject.toml"
        or path == root / "uv.lock"
        or _is_package_manifest(path)
        or path.name in {"package-lock.json", "npm-shrinkwrap.json"}
    )


def _project_package_name(name: object, project_name: str) -> bool:
    if not isinstance(name, str):
        return False
    leaf = name.rsplit("/", maxsplit=1)[-1]
    expected = _canonicalize_name(project_name)
    normalized_leaf = _canonicalize_name(leaf)
    return normalized_leaf in {expected, f"{expected}-webui"} or normalized_leaf.startswith(
        f"{expected}-"
    )


def _canonicalize_name(name: str) -> str:
    """Normalize distribution names without requiring project dependencies."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _load_json_object(path: Path, root: Path | None = None) -> dict[str, Any]:
    display_path = _relative(path, root) if root is not None else path.as_posix()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{display_path}: invalid JSON manifest: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{display_path}: package manifest must be a JSON object")
    return data


def _discover_package_manifests(root: Path) -> list[Path]:
    project_name, _ = _read_project_metadata(root)
    manifests: list[Path] = []
    for path in root.rglob("package.json"):
        if _is_skipped(path, root):
            continue
        package = _load_json_object(path, root)
        if _project_package_name(package.get("name"), project_name):
            manifests.append(path)
    return sorted(manifests)


def _npm_lock_paths(manifest: Path) -> list[Path]:
    return [
        path
        for path in (
            manifest.with_name("package-lock.json"),
            manifest.with_name("npm-shrinkwrap.json"),
        )
        if path.is_file()
    ]


def _text_carrier_type(path: Path, root: Path) -> str | None:
    relative = _relative(path, root)
    if path.name == "Dockerfile" or path.name.startswith("Dockerfile."):
        return "dockerfile"
    if relative.startswith(".github/workflows/"):
        return "github-actions"
    if path.suffix.lower() == ".ps1":
        return "powershell"
    if path.suffix.lower() == ".sh":
        return "shell"
    if relative.startswith("docs/") or path.name in {"README.md", "README.rst"}:
        return "documentation"
    if path.suffix.lower() in TEXT_VERSION_SUFFIXES:
        return "source-or-config"
    return None


def _exclusion_reason(path: Path, root: Path) -> str | None:
    relative = _relative(path, root)
    if relative in TRANSACTION_PROTECTED_FILES:
        return "user-owned or secret-bearing file"
    if relative in EXCLUDED_FILE_REASONS:
        return EXCLUDED_FILE_REASONS[relative]
    if HISTORICAL_UPGRADE_GUIDE_PATTERN.fullmatch(relative):
        return "historical upgrade guide"
    if relative.startswith("docs/superpowers/"):
        return "planning and generated task material"
    for part in path.relative_to(root).parts:
        if part in EXCLUDED_DIRECTORY_REASONS:
            return EXCLUDED_DIRECTORY_REASONS[part]
    return None


def _read_text_if_small(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_VERSION_SCAN_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _release_tokens(contents: str) -> tuple[str, ...]:
    """Return stable token data used to detect drift in indexed carriers."""
    return tuple(match.group(0) for match in ANY_RELEASE_TOKEN_PATTERN.finditer(contents))


def _normalize_release_token(token: str) -> str | None:
    match = ANY_RELEASE_TOKEN_PATTERN.fullmatch(token)
    if match is None:
        return None
    release = match.group("release")
    stage = match.group("stage")
    number = match.group("number")
    if stage in {"alpha", "beta"}:
        stage = {"alpha": "a", "beta": "b"}[stage]
    return f"{release}{stage or ''}{number or ''}"


def _version_spellings(project_version: str) -> set[str]:
    npm_version = to_npm_version(project_version)
    return {
        project_version,
        npm_version,
        to_git_tag(project_version),
        _normalize_release_token(project_version) or project_version,
    }


def _canonical_versions(tokens: tuple[str, ...] | list[str] | set[str]) -> set[str]:
    return {
        normalized
        for token in tokens
        if (normalized := _normalize_release_token(token)) is not None
    }


def _load_artifact_index(root: Path) -> tuple[list[ArtifactIndex], set[str]]:
    path = _artifact_index_path(root)
    if not path.is_file():
        return [], set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{ARTIFACT_INDEX_NAME}: invalid artifact index: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("artifacts"), list):
        raise ValueError(f"{ARTIFACT_INDEX_NAME}: expected an artifacts list")
    known_versions = data.get("known_versions", [])
    if not isinstance(known_versions, list) or not all(
        isinstance(version, str) for version in known_versions
    ):
        raise ValueError(f"{ARTIFACT_INDEX_NAME}: malformed known_versions list")
    records: list[ArtifactIndex] = []
    for item in data["artifacts"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError(f"{ARTIFACT_INDEX_NAME}: malformed artifact record")
        tokens = item.get("tokens")
        if not isinstance(tokens, list) or not all(isinstance(token, str) for token in tokens):
            raise ValueError(f"{ARTIFACT_INDEX_NAME}: malformed token list")
        records.append(ArtifactIndex(item["path"], tuple(tokens)))
    return records, _canonical_versions(known_versions)


def _write_artifact_index(
    root: Path,
    records: list[VersionArtifact],
    known_versions: set[str] | None = None,
) -> None:
    current_version = _read_project_version(root)
    known_versions = set(known_versions or ())
    known_versions.update(_canonical_versions(_version_spellings(current_version)))
    payload = {
        "schema": 1,
        "known_versions": sorted(known_versions),
        "artifacts": [
            {
                "path": _relative(record.path, root),
                "tokens": list(_release_tokens(_read_text_if_small(record.path) or "")),
            }
            for record in records
            if record.active and not _is_structured_artifact(record.path, root)
        ],
    }
    _write_text(
        _artifact_index_path(root),
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _project_release_base(project_version: str) -> str:
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:(?:a|b|rc)\d+)?", project_version)
    if match is None:
        raise ValueError(f"unsupported project version {project_version!r}")
    return match.group(1)


def _text_version_pattern(project_version: str) -> re.Pattern[str]:
    base = re.escape(_project_release_base(project_version))
    # Match PEP 440, npm, and Git-tag spellings for this release base.  This
    # catches stale prerelease carriers after a release number is advanced.
    return re.compile(
        rf"(?<![A-Za-z0-9])(?P<tag>v?)(?P<base>{base})"
        r"(?P<separator>-?)(?P<stage>a|b|rc)?(?P<number>\d+)?"
        r"(?![A-Za-z0-9])"
    )


def _discover_text_records(root: Path, project_version: str) -> list[VersionArtifact]:
    pattern = _text_version_pattern(project_version)
    records: list[VersionArtifact] = []
    for path in root.rglob("*"):
        if not path.is_file() or _is_skipped(path, root):
            continue
        if _is_package_manifest(path) or path.name in {
            "package-lock.json",
            "npm-shrinkwrap.json",
            "pyproject.toml",
            "uv.lock",
        }:
            continue
        carrier_type = _text_carrier_type(path, root)
        if carrier_type is None:
            continue
        contents = _read_text_if_small(path)
        if contents is None or not pattern.search(contents):
            continue
        reason = _exclusion_reason(path, root)
        records.append(
            VersionArtifact(
                path=path,
                carrier_type=carrier_type,
                active=reason is None,
                exclusion_reason=reason,
            )
        )
    return sorted(records, key=lambda record: record.path.as_posix())


def _discover_version_artifact_records_unlocked(root: Path) -> list[VersionArtifact]:
    """Return structured and text carriers with an auditable active state."""
    root = Path(root)
    project_name, project_version = _read_project_metadata(root)
    del project_name  # Metadata validation is intentional; name is used by manifests.
    records = [
        VersionArtifact(root / "pyproject.toml", "python-project", True),
        VersionArtifact(root / "uv.lock", "uv-lock", True),
    ]
    for manifest in _discover_package_manifests(root):
        records.append(VersionArtifact(manifest, "npm-package", True))
        records.extend(
            VersionArtifact(lock, "npm-lock", True) for lock in _npm_lock_paths(manifest)
        )
    records.extend(_discover_text_records(root, project_version))
    return list(dict.fromkeys(records))


def discover_version_artifact_records(root: Path) -> list[VersionArtifact]:
    """Return artifact records from one consistent workspace snapshot."""
    root = Path(root)
    with _version_sync_lock(root):
        return _discover_version_artifact_records_unlocked(root)


def discover_version_artifacts(root: Path) -> list[Path]:
    """Compatibility API returning only active artifact paths."""
    return [record.path for record in discover_version_artifact_records(Path(root)) if record.active]


def _write_package_version(path: Path, project_version: str) -> None:
    package = _load_json_object(path)
    package["version"] = to_npm_version(project_version)
    _write_text(path, json.dumps(package, ensure_ascii=False, indent=2) + "\n")


def _write_npm_lock_version(path: Path, project_version: str) -> None:
    lock = _load_json_object(path)
    npm_version = to_npm_version(project_version)
    lock["version"] = npm_version
    packages = lock.get("packages")
    if isinstance(packages, dict) and isinstance(packages.get(""), dict):
        packages[""]["version"] = npm_version
    _write_text(path, json.dumps(lock, ensure_ascii=False, indent=2) + "\n")


def _replacement_for_text_version(match: re.Match[str], project_version: str) -> str:
    npm_version = to_npm_version(project_version)
    replacement = npm_version if match.group("separator") == "-" else project_version
    if match.group("stage") is None:
        replacement = project_version
    if match.group("tag"):
        replacement = f"v{replacement.removeprefix('v')}"
    return replacement


def _sync_text_version_artifacts(root: Path, source_version: str, target_version: str) -> None:
    pattern = _text_version_pattern(source_version)
    for record in _discover_text_records(root, source_version):
        if not record.active:
            continue
        contents = _read_text_if_small(record.path)
        if contents is None:
            continue
        updated = pattern.sub(
            lambda match: _replacement_for_text_version(match, target_version), contents
        )
        if updated != contents:
            _write_text(record.path, updated)


def _uv_command() -> list[str]:
    executable = shutil.which("uv")
    if executable:
        return [executable]
    try:
        __import__("uv")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "uv is required to regenerate uv.lock; install uv or add it to PATH"
        ) from error
    return [sys.executable, "-m", "uv"]


def _iter_workspace_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, directories, names in os.walk(root):
        directories[:] = sorted(
            name for name in directories if name not in SKIP_SCAN_DIRECTORIES
        )
        files.extend(Path(current) / name for name in names)
    return files


def _is_transaction_protected(path: Path, root: Path) -> bool:
    """Keep private runtime files outside the release-sync rollback journal."""
    relative = _relative(path, root)
    return (
        relative in TRANSACTION_PROTECTED_FILES
        or path.name == ".env"
        or path.name.endswith(".hash")
        or path.is_relative_to(root / "data")
    )


def _validate_workspace_target(path: Path, root: Path) -> None:
    """Reject symlinks, directories, and targets that escape the workspace."""
    root_resolved = Path(root).resolve()
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root_resolved) or path.is_symlink():
        raise RuntimeError(
            f"refusing to synchronize unsafe workspace target {_relative(path, root)!r}"
        )
    if path.exists() and not path.is_file():
        raise RuntimeError(
            f"refusing to synchronize non-file workspace target {_relative(path, root)!r}"
        )


def _snapshot_workspace(
    root: Path, paths: list[Path] | tuple[Path, ...] | None = None
) -> dict[str, bytes]:
    """Capture existing files, optionally restricted to synchronization targets."""
    snapshot: dict[str, bytes] = {}
    candidates = _iter_workspace_files(root) if paths is None else paths
    for path in candidates:
        _validate_workspace_target(path, root)
        if _is_transaction_protected(path, root):
            continue
        if not path.is_file():
            continue
        try:
            snapshot[_relative(path, root)] = path.read_bytes()
        except OSError as error:
            raise RuntimeError(
                f"cannot snapshot synchronization target {_relative(path, root)!r}: {error}"
            ) from error
    return snapshot


def _missing_workspace_targets(
    root: Path, paths: list[Path] | tuple[Path, ...]
) -> set[str]:
    """Record synchronization targets that did not exist before the operation."""
    missing: set[str] = set()
    for path in paths:
        _validate_workspace_target(path, root)
        if not _is_transaction_protected(path, root) and not path.exists():
            missing.add(_relative(path, root))
    return missing


def _restore_workspace(
    root: Path,
    snapshot: dict[str, bytes],
    missing: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Restore only files owned by the version sync; preserve unrelated new files."""
    for relative in missing:
        path = root / Path(relative)
        _validate_workspace_target(path, root)
        if _is_transaction_protected(path, root):
            raise RuntimeError(
                f"refusing to restore protected synchronization target {relative!r}"
            )
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.exists():
            raise RuntimeError(
                f"cannot remove unexpected directory at synchronization target {relative!r}"
            )
    for relative, contents in snapshot.items():
        path = root / Path(relative)
        _validate_workspace_target(path, root)
        if _is_transaction_protected(path, root):
            raise RuntimeError(
                f"refusing to restore protected synchronization target {relative!r}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)


def _version_transaction_path(root: Path) -> Path:
    return root / VERSION_TRANSACTION_NAME


def _version_transaction_temp_path(root: Path) -> Path:
    return root / VERSION_TRANSACTION_TEMP_NAME


def _assert_transaction_directory(root: Path, path: Path, label: str) -> None:
    root_resolved = Path(root).resolve()
    path_resolved = path.resolve(strict=False)
    if path_resolved.parent != root_resolved or path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"refusing to use invalid {label} outside the project")


def _remove_version_transaction(root: Path) -> None:
    root = Path(root)
    path = _version_transaction_path(root)
    if path.exists():
        _assert_transaction_directory(root, path, "version transaction")
        shutil.rmtree(path)


def _persist_version_transaction(
    root: Path,
    snapshot: dict[str, bytes],
    missing: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Persist the pre-sync workspace so an interrupted sync can be undone."""
    temporary = _version_transaction_temp_path(root)
    final = _version_transaction_path(root)
    if temporary.exists():
        _assert_transaction_directory(root, temporary, "version transaction staging path")
        shutil.rmtree(temporary)
    if final.exists():
        raise RuntimeError(
            "a previous version transaction is still present; recover it before synchronizing"
        )
    temporary.mkdir(parents=True)
    try:
        for relative, contents in snapshot.items():
            path = temporary / Path("snapshot") / Path(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_durable_bytes(path, contents)
        manifest = {
            "schema": 2,
            "phase": "prepared",
            "files": sorted(snapshot),
            "missing": sorted(missing),
        }
        _write_durable_bytes(
            temporary / "manifest.json",
            (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
        )
        _fsync_directory(temporary / "snapshot")
        _fsync_directory(temporary)
        os.replace(temporary, final)
        _fsync_directory(root)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _recover_version_transaction(root: Path) -> None:
    """Restore a complete pre-sync snapshot left by an interrupted process."""
    root = Path(root)
    temporary = _version_transaction_temp_path(root)
    if temporary.exists():
        _assert_transaction_directory(root, temporary, "version transaction staging path")
        shutil.rmtree(temporary)
    transaction = _version_transaction_path(root)
    if not transaction.exists():
        return
    _assert_transaction_directory(root, transaction, "version transaction")
    manifest_path = transaction / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot recover version transaction: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("schema") not in {1, 2}:
        raise RuntimeError("cannot recover version transaction: unsupported manifest")
    files = manifest.get("files")
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise RuntimeError("cannot recover version transaction: malformed file list")
    missing = manifest.get("missing", [])
    if not isinstance(missing, list) or not all(
        isinstance(item, str) for item in missing
    ):
        raise RuntimeError("cannot recover version transaction: malformed missing list")
    snapshot_root = transaction / "snapshot"
    if not snapshot_root.is_dir() or snapshot_root.is_symlink():
        raise RuntimeError("cannot recover version transaction: missing snapshot directory")
    snapshot_root_resolved = snapshot_root.resolve()

    def normalize_transaction_paths(values: list[str], label: str) -> set[str]:
        normalized_values: set[str] = set()
        for relative in values:
            normalized = relative.replace("\\", "/")
            parts = normalized.split("/")
            if (
                not normalized
                or normalized.startswith("/")
                or re.match(r"^[A-Za-z]:/", normalized)
                or any(part in {"", ".", ".."} for part in parts)
                or normalized in normalized_values
            ):
                raise RuntimeError(
                    f"cannot recover version transaction: unsafe {label} path {relative!r}"
                )
            normalized_values.add(normalized)
        return normalized_values

    normalized_files = normalize_transaction_paths(files, "snapshot")
    normalized_missing = normalize_transaction_paths(missing, "missing")
    if normalized_files.intersection(normalized_missing):
        raise RuntimeError(
            "cannot recover version transaction: snapshot and missing paths overlap"
        )
    for label, paths in (("snapshot", normalized_files), ("missing", normalized_missing)):
        for normalized in paths:
            target = root / Path(*normalized.split("/"))
            if _is_transaction_protected(target, root):
                raise RuntimeError(
                    f"cannot recover version transaction: protected {label} path {normalized!r}"
                )

    snapshot: dict[str, bytes] = {}
    for normalized in normalized_files:
        parts = normalized.split("/")
        snapshot_path = snapshot_root / Path(*parts)
        resolved = snapshot_path.resolve(strict=False)
        if (
            resolved != snapshot_root_resolved / Path(*parts)
            or snapshot_path.is_symlink()
            or not snapshot_path.is_file()
        ):
            raise RuntimeError(
                f"cannot recover version transaction: invalid snapshot file {normalized!r}"
            )
        snapshot[normalized] = snapshot_path.read_bytes()
    _restore_workspace(root, snapshot, frozenset(normalized_missing))
    _remove_version_transaction(root)


def recover_version_transaction(root: Path) -> None:
    """Recover a release metadata sync before another sync or validation."""
    root = Path(root)
    if not root.is_dir():
        return
    with _version_sync_lock(root):
        _recover_version_transaction(root)


def _version_lock_path(root: Path) -> Path:
    return Path(root) / VERSION_LOCK_NAME


@contextmanager
def _version_sync_lock(root: Path) -> Iterator[None]:
    """Serialize release metadata recovery and synchronization per workspace.

    The lock is held by the operating system rather than by a PID marker, so a
    process crash releases it automatically and cannot leave a stale lock that
    blocks startup recovery.  The small lock file is ignored by Git and version
    carrier discovery.
    """
    root = Path(root)
    root = root.resolve()
    if not root.is_dir():
        yield
        return
    owner = threading.get_ident()
    with _VERSION_LOCK_STATE_GUARD:
        current = _VERSION_LOCK_STATE.get(root)
        if current is not None:
            handle, current_owner, depth = current
            if current_owner != owner:
                raise RuntimeError(
                    "another version synchronization is already in progress; "
                    "wait for it to finish before retrying"
                )
            _VERSION_LOCK_STATE[root] = (handle, owner, depth + 1)
            reentrant = True
        else:
            handle = _version_lock_path(root).open("a+b")
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            _VERSION_LOCK_STATE[root] = (handle, owner, 1)
            reentrant = False
    try:
        if not reentrant:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                with _VERSION_LOCK_STATE_GUARD:
                    _VERSION_LOCK_STATE.pop(root, None)
                handle.close()
                raise RuntimeError(
                    "another version synchronization is already in progress; "
                    "wait for it to finish before retrying"
                ) from error
        try:
            yield
        finally:
            with _VERSION_LOCK_STATE_GUARD:
                _, _, depth = _VERSION_LOCK_STATE[root]
                if depth > 1:
                    _VERSION_LOCK_STATE[root] = (handle, owner, depth - 1)
                else:
                    _VERSION_LOCK_STATE.pop(root, None)
                    try:
                        handle.seek(0)
                        if os.name == "nt":
                            import msvcrt

                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                    handle.close()
    finally:
        # The lock file itself remains as a stable inode. Removing it while a
        # waiting process has already opened the file would split the lock
        # namespace and permit two writers to proceed concurrently.
        pass


def _set_version_unlocked(root: Path, project_version: str) -> None:
    """Set the source version and regenerate every discovered derived carrier."""
    root = Path(root)
    to_npm_version(project_version)
    _recover_version_transaction(root)
    source_version = _read_project_version(root)
    _, known_versions = _load_artifact_index(root)
    known_versions.update(_canonical_versions(_version_spellings(source_version)))
    manifests = _discover_package_manifests(root)
    text_records = _discover_text_records(root, source_version)
    synchronization_targets = [
        root / "pyproject.toml",
        root / "uv.lock",
        _artifact_index_path(root),
        *manifests,
        *(lock for manifest in manifests for lock in _npm_lock_paths(manifest)),
        *(record.path for record in text_records if record.active),
    ]
    snapshot = _snapshot_workspace(root, synchronization_targets)
    missing = _missing_workspace_targets(root, synchronization_targets)
    _persist_version_transaction(root, snapshot, missing)
    try:
        _write_project_version(root, project_version)
        for manifest in manifests:
            _write_package_version(manifest, project_version)
            for lock_path in _npm_lock_paths(manifest):
                _write_npm_lock_version(lock_path, project_version)
        _sync_text_version_artifacts(root, source_version, project_version)
        result = subprocess.run([*_uv_command(), "lock"], cwd=root, check=False)
        if result.returncode:
            raise RuntimeError(f"uv lock failed with exit code {result.returncode}")
        _write_artifact_index(root, discover_version_artifact_records(root), known_versions)
        errors = check_versions(root)
        if errors:
            raise RuntimeError("version synchronization failed:\n" + "\n".join(errors))
        _remove_version_transaction(root)
    except Exception as error:
        try:
            _recover_version_transaction(root)
        except Exception as recovery_error:
            raise RuntimeError(
                f"version synchronization failed: {error}; "
                f"transaction recovery also failed: {recovery_error}"
            ) from error
        raise


def set_version(root: Path, project_version: str) -> None:
    """Set the source version and regenerate every discovered derived carrier."""
    with _version_sync_lock(Path(root)):
        _set_version_unlocked(Path(root), project_version)


def _git_output(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"git {' '.join(arguments)} timed out after 30 seconds"
        ) from error
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise GitCommandError(tuple(arguments), result.returncode, detail)
    return result.stdout


def _try_git_output(root: Path, *arguments: str) -> str | None:
    """Return output for the one optional probe: a missing branch upstream."""
    try:
        return _git_output(root, *arguments)
    except GitCommandError as error:
        upstream_probe = (
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        )
        if (
            tuple(arguments) == upstream_probe
            and error.returncode == 128
            and re.search(
                r"no upstream configured|has no upstream branch",
                str(error),
                flags=re.IGNORECASE,
            )
        ):
            return None
        raise


def _validate_remote_name(remote: str) -> str:
    """Accept ordinary Git remote names without allowing option injection."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", remote):
        raise ValueError(f"invalid Git remote name {remote!r}")
    return remote


def _validate_git_tag_name(tag: str) -> str:
    """Accept only tags this tool can generate before passing them to Git."""
    if not isinstance(tag, str) or not re.fullmatch(
        r"v\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", tag
    ):
        raise ValueError(
            f"invalid Git tag name {tag!r}; expected vX.Y.Z, vX.Y.ZaN, "
            "vX.Y.ZbN, or vX.Y.ZrcN"
        )
    return tag


def _validate_commit_name(commit: str) -> str:
    """Accept full or abbreviated hexadecimal commit names without options."""
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9A-Fa-f]{7,64}", commit):
        raise ValueError(f"invalid Git commit name {commit!r}")
    return commit


def _validate_object_id(value: str, label: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", normalized):
        raise RuntimeError(f"Git returned an invalid {label} object id: {value!r}")
    return normalized


def _local_git_tag_identity(root: Path, tag: str) -> TagIdentity:
    """Resolve a local lightweight or annotated tag to its release commit."""
    tag = _validate_git_tag_name(tag)
    ref = f"refs/tags/{tag}"
    object_id = _validate_object_id(
        _git_output(root, "rev-parse", "--verify", ref), "tag"
    )
    object_type = _git_output(root, "cat-file", "-t", object_id).strip()
    if object_type not in {"commit", "tag"}:
        raise RuntimeError(
            f"Git tag {tag!r} resolves to unsupported object type {object_type!r}; "
            "expected a commit or annotated tag"
        )
    commit = _validate_object_id(
        _git_output(root, "rev-parse", "--verify", f"{ref}^{{commit}}"), "commit"
    )
    return TagIdentity(tag, object_id, object_type, commit)


def _remote_git_tag_identity(root: Path, remote: str, tag: str) -> TagIdentity:
    """Resolve a remote tag without trusting the local tag namespace."""
    remote = _validate_remote_name(remote)
    tag = _validate_git_tag_name(tag)
    ref = f"refs/tags/{tag}"
    peeled_ref = f"{ref}^{{}}"
    output = _git_output(
        root,
        "ls-remote",
        "--tags",
        remote,
        ref,
        peeled_ref,
    )
    records: dict[str, str] = {}
    for line in output.splitlines():
        object_id, separator, found_ref = line.partition("\t")
        if not separator or found_ref not in {ref, peeled_ref}:
            continue
        records[found_ref] = _validate_object_id(object_id, "remote tag")
    object_id = records.get(ref)
    if object_id is None:
        raise RuntimeError(f"remote {remote!r} has no Git tag {tag!r}")
    peeled = records.get(peeled_ref)
    return TagIdentity(tag, object_id, "tag" if peeled else "commit", peeled or object_id)


def verify_tag_identity(
    root: Path,
    tag: str,
    *,
    expected_commit: str | None = None,
    expect_head: bool = False,
    remote: str | None = None,
    local_only: bool = False,
) -> dict[str, Any]:
    """Verify that a release tag, checkout, and optional remote are one identity.

    The project version remains the source of the tag spelling.  This function
    verifies the immutable object behind that spelling so a release cannot
    accidentally build from a different branch, retagged commit, or remote.
    """
    root = Path(root)
    if remote and local_only:
        raise ValueError("--remote and --local-only cannot be used together")
    tag = _validate_git_tag_name(tag)
    project_version = _read_project_version(root)
    expected_tag = to_git_tag(project_version)
    if tag != expected_tag:
        raise RuntimeError(
            f"Git tag does not match pyproject.toml: expected {expected_tag}, found {tag}"
        )

    local = _local_git_tag_identity(root, tag)
    head = _validate_object_id(
        _git_output(root, "rev-parse", "--verify", "HEAD"), "HEAD"
    )
    head_matches = head == local.commit
    if expect_head and not head_matches:
        raise RuntimeError(
            f"Git HEAD {head} does not match tag {tag} release commit {local.commit}"
        )

    expected_commit_value = None
    if expected_commit is not None:
        expected_commit = _validate_commit_name(expected_commit)
        expected_commit_value = _validate_object_id(
            _git_output(root, "rev-parse", "--verify", f"{expected_commit}^{{commit}}"),
            "expected commit",
        )
        if local.commit != expected_commit_value:
            raise RuntimeError(
                f"Git tag {tag} points to {local.commit}, expected commit "
                f"{expected_commit_value}"
            )

    resolved_remote = None
    remote_identity = None
    remote_matches = None
    if not local_only:
        resolved_remote = _validate_remote_name(remote or resolve_release_remote(root))
        remote_identity = _remote_git_tag_identity(root, resolved_remote, tag)
        remote_matches = (
            remote_identity.object_id == local.object_id
            and remote_identity.commit == local.commit
            and remote_identity.object_type == local.object_type
        )
        if not remote_matches:
            raise RuntimeError(
                f"remote {resolved_remote!r} Git tag {tag} does not match the local "
                f"release identity: local {local.object_id}/{local.commit}, "
                f"remote {remote_identity.object_id}/{remote_identity.commit}"
            )

    return {
        "tag": local.tag,
        "object_id": local.object_id,
        "object_type": local.object_type,
        "commit": local.commit,
        "head": head,
        "head_matches": head_matches,
        "expected_commit": expected_commit_value,
        "remote": resolved_remote,
        "remote_object_id": remote_identity.object_id if remote_identity else None,
        "remote_commit": remote_identity.commit if remote_identity else None,
        "remote_matches": remote_matches,
    }


def resolve_release_remote(root: Path) -> str:
    """Resolve the remote that owns the current release line.

    The branch upstream is authoritative.  If no upstream is configured,
    ``origin`` is accepted when present, followed by the only configured
    remote.  Ambiguous or remote-less repositories must opt into ``--local-only``.
    """
    root = Path(root)
    upstream = _try_git_output(
        root,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
    )
    if upstream is not None:
        normalized_upstream = upstream.strip()
        remote, separator, branch = normalized_upstream.partition("/")
        if not separator or not remote or not branch:
            raise RuntimeError(
                "Git upstream probe returned a malformed ref; pass --remote NAME "
                "or explicitly choose --local-only"
            )
        return _validate_remote_name(remote)
    remotes_output = _try_git_output(root, "remote")
    remotes = [line.strip() for line in (remotes_output or "").splitlines() if line.strip()]
    if "origin" in remotes:
        return "origin"
    if len(remotes) == 1:
        return remotes[0]
    if not remotes:
        raise RuntimeError(
            "no Git release remote could be determined; configure an upstream or "
            "pass --remote NAME, or explicitly choose --local-only"
        )
    raise RuntimeError(
        "multiple Git remotes are available but no upstream is configured; "
        "pass --remote NAME or explicitly choose --local-only"
    )


def _local_git_tags(root: Path) -> set[str]:
    return {
        tag.strip()
        for tag in _git_output(root, "tag", "--list").splitlines()
        if tag.strip()
    }


def _remote_git_tags(root: Path, remote: str) -> set[str]:
    remote = _validate_remote_name(remote)
    tags: set[str] = set()
    for line in _git_output(root, "ls-remote", "--tags", "--refs", remote).splitlines():
        _object_id, separator, ref = line.partition("\t")
        if separator and ref.startswith("refs/tags/"):
            tag = ref.removeprefix("refs/tags/")
            if "^" not in tag:
                tags.add(tag)
    return tags


def _normalized_versions(tags: set[str]) -> set[str]:
    """Keep only tags that name a recognized release version."""
    return {
        normalized
        for tag in tags
        if (normalized := _normalize_candidate_version(tag)) is not None
    }


def _sorted_versions(versions: set[str]) -> tuple[str, ...]:
    """Sort by release order, not lexically.

    字典序会把 `b9` 排在 `b10` 之后，读起来像「最高版本是 b9」。
    """
    return tuple(sorted(versions, key=lambda value: _release_sort_key(parse_version(value))))


def occupied_release_versions(
    root: Path,
    remote: str | None = None,
    *,
    local_only: bool = False,
) -> OccupancyReport:
    """Return every taken release version, split by whether it was published.

    与 `occupied_git_versions()` 读同一批 Tag，只是不再把来源丢掉。
    两侧都有的版本记为 ``released``——推成功之后本地那份不再是「仅本地」，
    而「已发布」是更强的论断。

    ``local_only=True`` 时远端集合为空而不是未知：那是显式声明「不查远端」。
    """
    root = Path(root)
    if remote and local_only:
        raise ValueError("--remote and --local-only cannot be used together")
    if remote:
        remote = _validate_remote_name(remote)
    local = _normalized_versions(_local_git_tags(root))
    published: set[str] = set()
    if not local_only:
        remote = remote or resolve_release_remote(root)
        published = _normalized_versions(_remote_git_tags(root, remote))
    return OccupancyReport(
        all_versions=_sorted_versions(local | published),
        released=_sorted_versions(published),
        reserved_locally=_sorted_versions(local - published),
    )


def occupied_git_versions(
    root: Path,
    remote: str | None = None,
    *,
    local_only: bool = False,
) -> set[str]:
    """Return recognized local and optional remote release versions.

    保留为全集：既有调用方与 JSON 消费者不受 `OccupancyReport` 的引入影响。
    需要知道「为什么被占用」时用 `occupied_release_versions()`。
    """
    return set(
        occupied_release_versions(root, remote, local_only=local_only).all_versions
    )


def _build_release_plan_unlocked(
    root: Path,
    *,
    kind: str | None = None,
    remote: str | None = None,
    local_only: bool = False,
) -> ReleasePlan:
    """Resolve a complete release identity without modifying the workspace."""
    root = Path(root)
    if remote and local_only:
        raise ValueError("--remote and --local-only cannot be used together")
    resolved_remote = None if local_only else (
        _validate_remote_name(remote) if remote else resolve_release_remote(root)
    )
    current_version = _read_project_version(root)
    # 读一次 Tag 就同时拿到「占用全集」与「为什么被占用」。碰撞判定只看全集，
    # 因此行为与本改动之前逐字节一致；来源信息只是不再被丢掉（需求 23.2）。
    occupancy = occupied_release_versions(
        root,
        remote=resolved_remote,
        local_only=local_only,
    )
    occupied = set(occupancy.all_versions)
    candidate = next_version(
        current_version,
        kind=kind,
        occupied_versions=occupied,
    )
    canonical_kind = _canonical_release_kind(kind)
    return ReleasePlan(
        current=current_version,
        candidate=candidate,
        npm=to_npm_version(candidate),
        tag=to_git_tag(candidate),
        kind=canonical_kind or "auto",
        remote=resolved_remote,
        local_only=local_only,
        occupied=occupancy.all_versions,
        released=occupancy.released,
        reserved_locally=occupancy.reserved_locally,
    )


def build_release_plan(
    root: Path,
    *,
    kind: str | None = None,
    remote: str | None = None,
    local_only: bool = False,
) -> ReleasePlan:
    """Resolve a complete release identity while holding the workspace lock."""
    root = Path(root)
    with _version_sync_lock(root):
        return _build_release_plan_unlocked(
            root,
            kind=kind,
            remote=remote,
            local_only=local_only,
        )


def _release_plan_payload(plan: ReleasePlan) -> dict[str, Any]:
    """Return the stable machine-readable representation used by CLI automation."""
    return plan._asdict()


def verify_release_candidate(
    root: Path,
    candidate: str,
    *,
    kind: str | None = None,
    remote: str | None = None,
    local_only: bool = False,
) -> None:
    """Recompute a planned release immediately before writing.

    Checking only whether the exact candidate Tag exists is insufficient: a
    concurrent release may publish a different, higher Tag and make the old
    candidate stale without occupying its name.
    """
    root = Path(root)
    normalized_candidate = _normalize_candidate_version(candidate)
    if normalized_candidate is None:
        raise ValueError(
            f"unsupported release candidate {candidate!r}; expected X.Y.Z, "
            "X.Y.ZaN, X.Y.ZbN, or X.Y.ZrcN, optionally prefixed with v"
        )
    candidate = normalized_candidate
    occupied = occupied_git_versions(
        root,
        remote=remote,
        local_only=local_only,
    )
    if candidate in occupied:
        location = "local Git tags" if local_only else f"local or {remote} Git tags"
        raise RuntimeError(
            f"release candidate {candidate} became occupied in {location}; "
            "run the release plan again"
        )
    current_version = _read_project_version(root)
    refreshed_candidate = next_version(
        current_version,
        kind=kind,
        occupied_versions=occupied,
    )
    if refreshed_candidate != candidate:
        location = "local Git tags" if local_only else f"local or {remote} Git tags"
        raise RuntimeError(
            f"release candidate {candidate} is stale after rechecking {location}; "
            f"the current release plan resolves to {refreshed_candidate}; "
            "run the release plan again"
        )


def _ensure_clean_worktree(root: Path) -> None:
    status = _git_output(root, "status", "--porcelain")
    if status.strip():
        raise RuntimeError(
            "working tree is dirty; commit or stash existing changes, or pass --allow-dirty"
        )


def bump_version(
    root: Path,
    *,
    kind: str | None = None,
    remote: str | None = None,
    dry_run: bool = False,
    allow_dirty: bool = False,
    local_only: bool = False,
) -> str:
    """Resolve and optionally synchronize the next release version."""
    root = Path(root)
    with _version_sync_lock(root):
        _recover_version_transaction(root)
        if not dry_run and not allow_dirty:
            _ensure_clean_worktree(root)
        plan = build_release_plan(
            root,
            kind=kind,
            remote=remote,
            local_only=local_only,
        )
        if not dry_run:
            verify_release_candidate(
                root,
                plan.candidate,
                kind=kind,
                remote=plan.remote,
                local_only=plan.local_only,
            )
            set_version(root, plan.candidate)
        return plan.candidate


def _locked_project_version(root: Path, project_name: str) -> str | None:
    try:
        data = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise
    expected_name = _canonicalize_name(project_name)
    for package in data.get("package", []):
        if isinstance(package, dict) and _canonicalize_name(str(package.get("name", ""))) == expected_name:
            found = package.get("version")
            return found if isinstance(found, str) else None
    return None


def _check_versions_unlocked(root: Path, tag: str | None = None) -> list[str]:
    """Return every version drift and malformed active carrier."""
    root = Path(root)
    errors: list[str] = []
    try:
        project_name, project_version = _read_project_metadata(root)
    except (OSError, ValueError) as error:
        return [f"pyproject.toml: {error}"]

    expected_npm = to_npm_version(project_version)
    index_records: list[ArtifactIndex] = []
    known_versions: set[str] = set()
    try:
        index_records, known_versions = _load_artifact_index(root)
    except ValueError as error:
        errors.append(str(error))
    expected_canonical = _canonical_versions(_version_spellings(project_version))
    known_versions.update(expected_canonical)
    try:
        manifests = _discover_package_manifests(root)
    except ValueError as error:
        return [str(error)]
    for manifest in manifests:
        relative = _relative(manifest, root)
        try:
            package = _load_json_object(manifest, root)
            if package.get("version") != expected_npm:
                errors.append(
                    f"{relative}: expected version {expected_npm}, found {package.get('version')!r}"
                )
        except (OSError, ValueError) as error:
            errors.append(str(error))
        for lock_path in _npm_lock_paths(manifest):
            relative_lock = _relative(lock_path, root)
            try:
                lock = _load_json_object(lock_path, root)
                values = [lock.get("version")]
                packages = lock.get("packages")
                if isinstance(packages, dict) and isinstance(packages.get(""), dict):
                    values.append(packages[""] .get("version"))
                if any(value != expected_npm for value in values):
                    errors.append(
                        f"{relative_lock}: expected version {expected_npm}, found {values!r}"
                    )
            except (OSError, ValueError) as error:
                errors.append(str(error))

    try:
        locked_version = _locked_project_version(root, project_name)
        if locked_version != project_version:
            errors.append(
                f"uv.lock: expected {project_name} {project_version}, found {locked_version!r}"
            )
    except (OSError, ValueError) as error:
        errors.append(f"uv.lock: {error}")

    pattern = _text_version_pattern(project_version)
    records = discover_version_artifact_records(root)
    indexed_paths = {record.path for record in index_records}
    unindexed_current_paths: set[str] = set()
    index_present = _artifact_index_path(root).is_file()
    if index_present:
        for index_record in index_records:
            path = root / Path(index_record.path)
            contents = _read_text_if_small(path) if path.is_file() else None
            if (
                contents is None
                or _text_carrier_type(path, root) is None
                or _exclusion_reason(path, root) is not None
            ):
                errors.append(
                    f"{ARTIFACT_INDEX_NAME}: missing active artifact {index_record.path}"
                )
                continue
            current_tokens = _release_tokens(contents)
            current_versions = _canonical_versions(current_tokens)
            stale_versions = sorted(
                version
                for version in current_versions.intersection(known_versions)
                if version not in expected_canonical
            )
            if stale_versions:
                errors.append(
                    f"{index_record.path}: stale indexed release version(s) {stale_versions!r}"
                )
            if not current_versions.intersection(expected_canonical):
                errors.append(
                    f"{index_record.path}: indexed artifact does not contain {project_version}"
                )
        for record in records:
            relative = _relative(record.path, root)
            if (
                record.active
                and not _is_structured_artifact(record.path, root)
                and relative not in indexed_paths
                and pattern.search(_read_text_if_small(record.path) or "")
            ):
                unindexed_current_paths.add(relative)
                errors.append(
                    f"{ARTIFACT_INDEX_NAME}: active artifact is not indexed: {relative}"
                )
    historical_candidates: list[VersionArtifact] = []
    if known_versions:
        for path in root.rglob("*"):
            if not path.is_file() or _is_skipped(path, root):
                continue
            carrier_type = _text_carrier_type(path, root)
            if (
                carrier_type is None
                or _is_structured_artifact(path, root)
                or _exclusion_reason(path, root) is not None
            ):
                continue
            contents = _read_text_if_small(path)
            tokens = _canonical_versions(_release_tokens(contents or ""))
            if tokens.intersection(known_versions):
                historical_candidates.append(VersionArtifact(path, carrier_type, True))
        for candidate in historical_candidates:
            relative = _relative(candidate.path, root)
            if (
                index_present
                and relative not in indexed_paths
                and relative not in unindexed_current_paths
                and not _is_structured_artifact(candidate.path, root)
            ):
                errors.append(
                    f"{ARTIFACT_INDEX_NAME}: historical release carrier is not indexed: {relative}"
                )
    for record in records:
        if not record.active or record.carrier_type in {"python-project", "uv-lock", "npm-package", "npm-lock"}:
            continue
        contents = _read_text_if_small(record.path)
        if contents is None:
            continue
        stale = []
        for match in pattern.finditer(contents):
            found = match.group(0)
            expected = _replacement_for_text_version(match, project_version)
            if found != expected:
                stale.append((found, expected))
        if stale:
            errors.append(f"{_relative(record.path, root)}: stale release version(s) {stale!r}")

    if tag is not None and tag != to_git_tag(project_version):
        errors.append(f"git tag: expected {to_git_tag(project_version)}, found {tag!r}")
    return errors


def check_versions(root: Path, tag: str | None = None) -> list[str]:
    """Return version drift from one consistent workspace snapshot."""
    root = Path(root)
    with _version_sync_lock(root):
        return _check_versions_unlocked(root, tag)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("get", help="print the PEP 440 project version")
    subparsers.add_parser("npm", help="print the npm semver version")
    subparsers.add_parser("tag", help="print the Git release tag")
    subparsers.add_parser("discover", help="audit every discovered version artifact")
    next_parser = subparsers.add_parser(
        "next", help="preview the next non-conflicting release version"
    )
    next_parser.add_argument(
        "--kind",
        help="release channel: alpha, beta, rc, stable, patch, minor, or major",
    )
    next_parser.add_argument(
        "--remote",
        help="also check release tags from this Git remote, for example origin",
    )
    next_parser.add_argument(
        "--local-only",
        action="store_true",
        help="skip remote tag checks explicitly for offline development",
    )
    plan_parser = subparsers.add_parser(
        "plan", help="print the complete read-only release plan"
    )
    plan_parser.add_argument(
        "--kind",
        help="release channel: alpha, beta, rc, stable, patch, minor, or major",
    )
    plan_parser.add_argument(
        "--remote",
        help="also check release tags from this Git remote, for example origin",
    )
    plan_parser.add_argument(
        "--local-only",
        action="store_true",
        help="skip remote tag checks explicitly for offline development",
    )
    plan_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a stable JSON object for CI and release automation",
    )
    bump_parser = subparsers.add_parser(
        "bump", help="synchronize the next non-conflicting release version"
    )
    bump_parser.add_argument(
        "--kind",
        help="release channel: alpha, beta, rc, stable, patch, minor, or major",
    )
    bump_parser.add_argument(
        "--remote",
        help="also check release tags from this Git remote, for example origin",
    )
    bump_parser.add_argument(
        "--local-only",
        action="store_true",
        help="skip remote tag checks explicitly for offline development",
    )
    bump_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview the resolved version without changing files",
    )
    bump_parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow existing uncommitted changes while synchronizing",
    )
    set_parser = subparsers.add_parser("set", help="set and synchronize a version")
    set_parser.add_argument("version")
    check_parser = subparsers.add_parser("check", help="verify generated versions")
    check_parser.add_argument("--tag")
    verify_tag_parser = subparsers.add_parser(
        "verify-tag", help="verify a release tag and its immutable Git commit identity"
    )
    verify_tag_parser.add_argument("--tag", required=True)
    verify_tag_parser.add_argument(
        "--expected-commit",
        help="require the tag to resolve to this commit",
    )
    verify_tag_parser.add_argument(
        "--expect-head",
        action="store_true",
        help="require the checked-out HEAD to equal the tag commit",
    )
    verify_tag_parser.add_argument(
        "--remote",
        help="also verify the same tag object on this Git remote",
    )
    verify_tag_parser.add_argument(
        "--local-only",
        action="store_true",
        help="skip remote identity verification explicitly for offline development",
    )
    verify_tag_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a stable JSON object for CI and release automation",
    )
    return parser


def _run_cli_command(args: argparse.Namespace) -> int:
    """Execute a parsed command while the caller owns any needed lock."""
    if args.command == "bump":
        version = bump_version(
            PROJECT_ROOT,
            kind=args.kind,
            remote=args.remote,
            dry_run=args.dry_run,
            allow_dirty=args.allow_dirty,
            local_only=args.local_only,
        )
        action = "version candidate" if args.dry_run else "version synchronized"
        print(f"{action}: {version}")
        return 0
    if args.command == "set":
        set_version(PROJECT_ROOT, args.version)
        print(f"version synchronized: {args.version}")
        return 0
    if args.command == "verify-tag":
        identity = verify_tag_identity(
            PROJECT_ROOT,
            args.tag,
            expected_commit=args.expected_commit,
            expect_head=args.expect_head,
            remote=args.remote,
            local_only=args.local_only,
        )
        if args.json:
            print(json.dumps(identity, ensure_ascii=False, sort_keys=True))
        else:
            for key, value in identity.items():
                print(f"{key}: {value}")
        return 0

    project_version = _read_project_version(PROJECT_ROOT)
    if args.command == "get":
        print(project_version)
    elif args.command == "npm":
        print(to_npm_version(project_version))
    elif args.command == "tag":
        print(to_git_tag(project_version))
    elif args.command == "next":
        print(
            build_release_plan(
                PROJECT_ROOT,
                kind=args.kind,
                remote=args.remote,
                local_only=args.local_only,
            ).candidate
        )
    elif args.command == "plan":
        plan = build_release_plan(
            PROJECT_ROOT,
            kind=args.kind,
            remote=args.remote,
            local_only=args.local_only,
        )
        payload = _release_plan_payload(plan)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            for key in (
                "current",
                "candidate",
                "npm",
                "tag",
                "kind",
                "remote",
                "local_only",
                "occupied",
                # 「已发布」与「仅本地占号」必须分开打印：后者往往是上一次发布
                # 中断留下的残留，处置是删掉那个本地 Tag 再重试，
                # 而不是把版本号一路往上跳。
                "released",
                "reserved_locally",
            ):
                value = payload[key]
                if isinstance(value, tuple):
                    value = ",".join(value)
                print(f"{key}: {value}")
    elif args.command == "discover":
        for record in discover_version_artifact_records(PROJECT_ROOT):
            state = "active" if record.active else f"excluded: {record.exclusion_reason}"
            print(f"{state}\t{record.carrier_type}\t{_relative(record.path, PROJECT_ROOT)}")
    else:
        errors = check_versions(PROJECT_ROOT, args.tag)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print(f"version artifacts synchronized: {project_version}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command in {"bump", "set"}:
            return _run_cli_command(args)
        # Recovery and every read-only command share one lock window.  This
        # prevents `check`/`plan` from observing files halfway through a sync.
        with _version_sync_lock(PROJECT_ROOT):
            _recover_version_transaction(PROJECT_ROOT)
            return _run_cli_command(args)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"version error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
