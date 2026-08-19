#!/usr/bin/env python3
"""Synchronize release metadata from the single version in ``pyproject.toml``.

The command deliberately discovers carriers from the repository instead of
maintaining a list of filenames.  Human release work only changes the Python
project version; package manifests, lock files, source constants, CI, Docker,
and operational documentation are derived and checked here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 support; tomli is a project dependency.
    import tomli as tomllib  # type: ignore[no-redef]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_INDEX_NAME = ".version-artifacts.json"
VERSION_PATTERN = re.compile(r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?")
ANY_RELEASE_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])v?(?P<release>\d+\.\d+\.\d+)"
    r"(?P<separator>-?)(?P<stage>a|b|rc|alpha|beta)?(?P<number>\d+)?"
    r"(?![A-Za-z0-9])"
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
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        ".venv-win",
        "artifacts",
        "dist",
        "graphify-out",
        "logs",
        "node_modules",
        "work",
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
    "webui/UPSTREAM.md": "upstream version record",
    "scripts/version.py": "version synchronization implementation",
    ARTIFACT_INDEX_NAME: "version synchronization index",
}


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
    if relative in EXCLUDED_FILE_REASONS:
        return EXCLUDED_FILE_REASONS[relative]
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


def discover_version_artifact_records(root: Path) -> list[VersionArtifact]:
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


def _snapshot_workspace(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in _iter_workspace_files(root):
        try:
            snapshot[_relative(path, root)] = path.read_bytes()
        except OSError:
            continue
    return snapshot


def _restore_workspace(root: Path, snapshot: dict[str, bytes]) -> None:
    before = set(snapshot)
    for path in _iter_workspace_files(root):
        relative = _relative(path, root)
        if relative not in before:
            path.unlink(missing_ok=True)
    for relative, contents in snapshot.items():
        path = root / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)


def set_version(root: Path, project_version: str) -> None:
    """Set the source version and regenerate every discovered derived carrier."""
    root = Path(root)
    to_npm_version(project_version)
    source_version = _read_project_version(root)
    _, known_versions = _load_artifact_index(root)
    known_versions.update(_canonical_versions(_version_spellings(source_version)))
    snapshot = _snapshot_workspace(root)
    try:
        _write_project_version(root, project_version)
        for manifest in _discover_package_manifests(root):
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
    except Exception:
        _restore_workspace(root, snapshot)
        raise


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


def check_versions(root: Path, tag: str | None = None) -> list[str]:
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("get", help="print the PEP 440 project version")
    subparsers.add_parser("npm", help="print the npm semver version")
    subparsers.add_parser("tag", help="print the Git release tag")
    subparsers.add_parser("discover", help="audit every discovered version artifact")
    set_parser = subparsers.add_parser("set", help="set and synchronize a version")
    set_parser.add_argument("version")
    check_parser = subparsers.add_parser("check", help="verify generated versions")
    check_parser.add_argument("--tag")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        project_version = _read_project_version(PROJECT_ROOT)
        if args.command == "get":
            print(project_version)
        elif args.command == "npm":
            print(to_npm_version(project_version))
        elif args.command == "tag":
            print(to_git_tag(project_version))
        elif args.command == "discover":
            for record in discover_version_artifact_records(PROJECT_ROOT):
                state = "active" if record.active else f"excluded: {record.exclusion_reason}"
                print(f"{state}\t{record.carrier_type}\t{_relative(record.path, PROJECT_ROOT)}")
        elif args.command == "set":
            set_version(PROJECT_ROOT, args.version)
            print(f"version synchronized: {args.version}")
        else:
            errors = check_versions(PROJECT_ROOT, args.tag)
            if errors:
                for error in errors:
                    print(error, file=sys.stderr)
                return 1
            print(f"version artifacts synchronized: {project_version}")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"version error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
