import hashlib
import json
import os
import shutil
import stat
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from ruamel.yaml import YAML

from kirara_ai.config.config_loader import ConfigLoader
from kirara_ai.config.global_config import GlobalConfig


BACKUP_FORMAT_VERSION = 1
BACKUP_SUFFIX = ".kirara-backup.zip"
MANIFEST_NAME = "manifest.json"
BACKUP_DIRECTORY_NAME = "backups"
MAX_ARCHIVE_SIZE_BYTES = 1024 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE_BYTES = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_FILE_COUNT = 20_000
MAX_COMPRESSION_RATIO = 200
BACKUP_OPERATION_LOCK = threading.RLock()

DIRECTORY_COMPONENTS = frozenset(
    {
        "db",
        "dispatch_rules",
        "fonts",
        "media",
        "memory",
        "plugins",
        "web",
        "workflows",
    }
)
FILE_COMPONENTS = frozenset({"auto_detect_state.json", "config.yaml"})
ALLOWED_COMPONENTS = DIRECTORY_COMPONENTS | FILE_COMPONENTS
EXCLUDED_DIRECTORY_NAMES = frozenset({".git", ".venv", "__pycache__", "backups", "logs", "venv"})
EXCLUDED_FILE_SUFFIXES = frozenset({".pyc", ".pyo"})


class BackupValidationError(ValueError):
    """Raised when a backup archive is unsafe or incompatible."""


@dataclass(frozen=True)
class BackupFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class BackupManifest:
    format_version: int
    created_at: str
    application_version: str
    components: set[str]
    files: tuple[BackupFile, ...]
    uncompressed_size: int


@dataclass(frozen=True)
class RestoreResult:
    rollback_path: Path
    restored_components: list[str]


class BackupService:
    """Creates, validates and restores complete portable data backups."""

    def __init__(self, data_path: Path | str):
        self.data_path = Path(data_path).resolve()
        self.backup_path = self.data_path / BACKUP_DIRECTORY_NAME

    def create_backup(self, backup_kind: str = "backup") -> Path:
        """Create a complete backup without including prior backup archives."""
        if backup_kind not in {"backup", "export", "rollback"}:
            raise ValueError("unsupported backup kind")
        with BACKUP_OPERATION_LOCK:
            return self._create_backup(backup_kind)

    def _create_backup(self, backup_kind: str) -> Path:
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.backup_path.mkdir(parents=True, exist_ok=True)
        archive_path = self.backup_path / self._build_backup_filename(backup_kind)
        components = self._existing_components()
        files = list(self._iter_backup_files(components))
        manifest_files = [self._build_file_record(file_path) for file_path in files]
        manifest = {
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "application_version": self._application_version(),
            "components": sorted(components),
            "files": [file.__dict__ for file in manifest_files],
            "uncompressed_size": sum(file.size for file in manifest_files),
        }

        try:
            with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
                for file_path in files:
                    archive.write(file_path, file_path.relative_to(self.data_path).as_posix())
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise

        return archive_path

    def inspect_backup(self, archive_path: Path | str) -> BackupManifest:
        """Validate archive structure and checksums without modifying application data."""
        archive_path = Path(archive_path)
        if not archive_path.is_file():
            raise BackupValidationError("backup archive does not exist")
        if archive_path.stat().st_size > MAX_ARCHIVE_SIZE_BYTES:
            raise BackupValidationError("backup archive exceeds the maximum size")

        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                members = self._validate_archive_members(archive)
                manifest = self._read_manifest(archive)
                self._validate_manifest(manifest, members, archive)
        except zipfile.BadZipFile as error:
            raise BackupValidationError("invalid backup archive") from error

        return manifest

    def restore_backup(self, archive_path: Path | str) -> RestoreResult:
        """Restore a verified backup and roll back every changed component on failure."""
        with BACKUP_OPERATION_LOCK:
            return self._restore_backup(archive_path)

    def _restore_backup(self, archive_path: Path | str) -> RestoreResult:
        manifest = self.inspect_backup(archive_path)
        self.data_path.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="kirara-backup-", dir=self.data_path.parent) as temporary_directory:
            temporary_path = Path(temporary_directory)
            staged_path = temporary_path / "staged"
            previous_path = temporary_path / "previous"
            self._extract_backup(Path(archive_path), manifest, staged_path)
            self._validate_staged_data(staged_path, manifest)
            rollback_path = self.create_backup("rollback")
            restored_components: list[str] = []

            try:
                for component in sorted(manifest.components):
                    self._replace_component(
                        staged_path / component,
                        self.data_path / component,
                        previous_path / component,
                    )
                    restored_components.append(component)
            except Exception:
                self._restore_previous_components(restored_components, previous_path)
                raise

        return RestoreResult(rollback_path=rollback_path, restored_components=restored_components)

    def list_rollbacks(self) -> list[Path]:
        """Return only backup files created in the dedicated local rollback directory."""
        if not self.backup_path.is_dir():
            return []
        return sorted(
            (
                path
                for path in self.backup_path.iterdir()
                if path.is_file()
                and path.name.startswith("kirara-rollback-")
                and path.name.endswith(BACKUP_SUFFIX)
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def get_rollback(self, backup_name: str) -> Path:
        """Resolve a rollback archive by name without allowing arbitrary filesystem access."""
        if Path(backup_name).name != backup_name or not backup_name.endswith(BACKUP_SUFFIX):
            raise BackupValidationError("invalid rollback backup name")
        backup_path = self.backup_path / backup_name
        if not backup_path.is_file():
            raise BackupValidationError("rollback backup does not exist")
        return backup_path

    def _existing_components(self) -> set[str]:
        return {
            component
            for component in ALLOWED_COMPONENTS
            if (self.data_path / component).exists() or (self.data_path / component).is_symlink()
        }

    def _iter_backup_files(self, components: Iterable[str]) -> Iterable[Path]:
        for component in sorted(components):
            component_path = self.data_path / component
            self._assert_not_symbolic_link(component_path)
            if component in FILE_COMPONENTS:
                if component_path.is_file():
                    yield component_path
                continue

            for file_path in sorted(component_path.rglob("*")):
                if self._is_excluded_path(file_path):
                    continue
                self._assert_not_symbolic_link(file_path)
                if file_path.is_file():
                    yield file_path

    def _build_file_record(self, file_path: Path) -> BackupFile:
        return BackupFile(
            path=file_path.relative_to(self.data_path).as_posix(),
            sha256=self._calculate_sha256(file_path),
            size=file_path.stat().st_size,
        )

    def _validate_archive_members(self, archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
        members: dict[str, zipfile.ZipInfo] = {}
        uncompressed_size = 0
        for member in archive.infolist():
            if member.filename in members:
                raise BackupValidationError("backup archive contains duplicate members")
            self._validate_archive_path(member.filename)
            if stat.S_ISLNK(member.external_attr >> 16):
                raise BackupValidationError("backup archive contains symbolic links")
            if member.file_size and member.compress_size == 0:
                raise BackupValidationError("backup archive contains an invalid compressed member")
            if member.compress_size and member.file_size > member.compress_size * MAX_COMPRESSION_RATIO:
                raise BackupValidationError("backup archive contains an unsafe compression ratio")
            uncompressed_size += member.file_size
            if uncompressed_size > MAX_UNCOMPRESSED_SIZE_BYTES:
                raise BackupValidationError("backup archive exceeds the maximum uncompressed size")
            members[member.filename] = member

        if len(members) > MAX_ARCHIVE_FILE_COUNT:
            raise BackupValidationError("backup archive contains too many files")
        if MANIFEST_NAME not in members:
            raise BackupValidationError("backup manifest is missing")
        return members

    def _read_manifest(self, archive: zipfile.ZipFile) -> BackupManifest:
        try:
            payload = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            files = tuple(
                BackupFile(
                    path=str(item["path"]),
                    sha256=str(item["sha256"]),
                    size=int(item["size"]),
                )
                for item in payload["files"]
            )
            manifest = BackupManifest(
                format_version=int(payload["format_version"]),
                created_at=str(payload["created_at"]),
                application_version=str(payload["application_version"]),
                components=set(payload["components"]),
                files=files,
                uncompressed_size=int(payload["uncompressed_size"]),
            )
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BackupValidationError("backup manifest is invalid") from error

        if manifest.format_version != BACKUP_FORMAT_VERSION:
            raise BackupValidationError("backup format version is not supported")
        if not manifest.components.issubset(ALLOWED_COMPONENTS):
            raise BackupValidationError("backup manifest contains unsupported components")
        if manifest.uncompressed_size < 0 or manifest.uncompressed_size > MAX_UNCOMPRESSED_SIZE_BYTES:
            raise BackupValidationError("backup manifest has an invalid size")
        return manifest

    def _validate_manifest(
        self,
        manifest: BackupManifest,
        members: dict[str, zipfile.ZipInfo],
        archive: zipfile.ZipFile,
    ) -> None:
        manifest_paths = set()
        manifest_size = 0
        for file in manifest.files:
            self._validate_archive_path(file.path)
            if file.path == MANIFEST_NAME or file.path in manifest_paths:
                raise BackupValidationError("backup manifest contains duplicate file records")
            if file.size < 0 or len(file.sha256) != 64:
                raise BackupValidationError("backup manifest contains an invalid file record")
            if file.path not in members:
                raise BackupValidationError("backup manifest references a missing file")
            if members[file.path].is_dir() or members[file.path].file_size != file.size:
                raise BackupValidationError("backup manifest file size mismatch")
            if self._calculate_archive_member_sha256(archive, file.path) != file.sha256:
                raise BackupValidationError("backup checksum mismatch")
            if self._component_from_archive_path(file.path) not in manifest.components:
                raise BackupValidationError("backup manifest file is outside the declared components")
            manifest_paths.add(file.path)
            manifest_size += file.size

        archive_file_paths = {
            path for path, member in members.items() if path != MANIFEST_NAME and not member.is_dir()
        }
        if manifest_paths != archive_file_paths:
            raise BackupValidationError("backup manifest file list does not match the archive")
        if manifest_size != manifest.uncompressed_size:
            raise BackupValidationError("backup manifest total size mismatch")

    def _extract_backup(self, archive_path: Path, manifest: BackupManifest, staged_path: Path) -> None:
        staged_path.mkdir(parents=True)
        for component in manifest.components:
            if component in DIRECTORY_COMPONENTS:
                (staged_path / component).mkdir(parents=True)

        with zipfile.ZipFile(archive_path, "r") as archive:
            for file in manifest.files:
                target_path = staged_path / PurePosixPath(file.path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(file.path, "r") as source, target_path.open("xb") as target:
                    shutil.copyfileobj(source, target)

    def _validate_staged_data(self, staged_path: Path, manifest: BackupManifest) -> None:
        config_path = staged_path / "config.yaml"
        if "config.yaml" in manifest.components:
            try:
                ConfigLoader.load_config(str(config_path), GlobalConfig)
            except Exception as error:
                raise BackupValidationError("configuration validation failed") from error

        yaml = YAML(typ="safe")
        rules_path = staged_path / "dispatch_rules" / "rules.yaml"
        if rules_path.is_file():
            try:
                rules = yaml.load(rules_path.read_text(encoding="utf-8"))
                if not isinstance(rules, list):
                    raise TypeError("rules must be a list")
            except Exception as error:
                raise BackupValidationError("dispatch rule validation failed") from error

        workflows_path = staged_path / "workflows"
        if workflows_path.is_dir():
            for workflow_path in workflows_path.rglob("*.yaml"):
                try:
                    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"))
                    if not isinstance(workflow, dict) or not isinstance(workflow.get("blocks"), list):
                        raise TypeError("workflow must define blocks")
                except Exception as error:
                    raise BackupValidationError("workflow validation failed") from error

    def _replace_component(self, staged_component: Path, target_component: Path, previous_component: Path) -> None:
        previous_component.parent.mkdir(parents=True, exist_ok=True)
        moved_previous = False
        if target_component.exists() or target_component.is_symlink():
            os.replace(target_component, previous_component)
            moved_previous = True
        try:
            os.replace(staged_component, target_component)
        except Exception:
            if moved_previous and previous_component.exists():
                os.replace(previous_component, target_component)
            raise

    def _restore_previous_components(self, restored_components: list[str], previous_path: Path) -> None:
        for component in reversed(restored_components):
            target_component = self.data_path / component
            previous_component = previous_path / component
            self._remove_path(target_component)
            if previous_component.exists() or previous_component.is_symlink():
                os.replace(previous_component, target_component)

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()

    @staticmethod
    def _calculate_sha256(file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _calculate_archive_member_sha256(archive: zipfile.ZipFile, member_name: str) -> str:
        digest = hashlib.sha256()
        with archive.open(member_name, "r") as member:
            for chunk in iter(lambda: member.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _application_version() -> str:
        try:
            return version("kirara-ai")
        except PackageNotFoundError:
            return "unknown"

    @staticmethod
    def _build_backup_filename(backup_kind: str) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return f"kirara-{backup_kind}-{timestamp}{BACKUP_SUFFIX}"

    @staticmethod
    def _assert_not_symbolic_link(path: Path) -> None:
        if path.is_symlink():
            raise BackupValidationError("symbolic links cannot be included in a backup")

    @staticmethod
    def _is_excluded_path(path: Path) -> bool:
        return path.suffix in EXCLUDED_FILE_SUFFIXES or any(
            part in EXCLUDED_DIRECTORY_NAMES for part in path.parts
        )

    @staticmethod
    def _validate_archive_path(member_name: str) -> None:
        if "\\" in member_name:
            raise BackupValidationError("unsafe archive path")
        path = PurePosixPath(member_name)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise BackupValidationError("unsafe archive path")
        if member_name == MANIFEST_NAME:
            return
        BackupService._component_from_archive_path(member_name)

    @staticmethod
    def _component_from_archive_path(member_name: str) -> str:
        component = PurePosixPath(member_name).parts[0]
        if component in FILE_COMPONENTS and len(PurePosixPath(member_name).parts) != 1:
            raise BackupValidationError("unsafe archive path")
        if component not in ALLOWED_COMPONENTS:
            raise BackupValidationError("unsafe archive path")
        return component
