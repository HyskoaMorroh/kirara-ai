"""Versioned provider pricing and immutable request cost snapshots."""

from __future__ import annotations

import json
import hashlib
import shutil
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable, Optional

from filelock import FileLock, Timeout as FileLockTimeout
from pydantic import BaseModel, ConfigDict, Field, field_validator

from kirara_ai.llm.format.response import Usage, UsageSource
from kirara_ai.workflow.persistence import FileMutation, FileTransaction


PRICE_CATALOG_SCHEMA = "kirara-ai.price-catalog"
PRICE_CATALOG_SCHEMA_VERSION = 1
PRICE_CATALOG_FORMAT_VERSION = 1
PRICE_CATALOG_BACKUP_GENERATIONS = 3


class PriceCatalogConflictError(RuntimeError):
    """Raised when a persistent catalog changed since it was loaded."""


class PriceCatalogIntegrityError(ValueError):
    """Raised when a catalog document fails its canonical integrity check."""


class PriceCatalogLockError(TimeoutError):
    """Raised when the catalog lock cannot be acquired within its deadline."""


class PriceVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    version_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effective_from: datetime
    currency: str = Field(min_length=3, max_length=3)
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    cache_read_per_million: Decimal = Field(ge=0)
    cache_write_per_million: Decimal = Field(ge=0)

    @field_validator("effective_from")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective_from must include a timezone")
        return value.astimezone(timezone.utc)


class CostSnapshot(BaseModel):
    """Immutable cost facts captured at request completion."""

    model_config = ConfigDict(frozen=True)

    price_version_id: str
    provider: str
    model: str
    currency: str
    priced_at: datetime
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    input_cost: Optional[Decimal] = None
    output_cost: Optional[Decimal] = None
    cache_read_cost: Optional[Decimal] = None
    cache_write_cost: Optional[Decimal] = None
    total_cost: Optional[Decimal] = None
    usage_source: UsageSource = UsageSource.UNKNOWN


class PriceCatalog:
    """Versioned provider pricing with optional durable JSON persistence.

    A catalog created with the original iterable-only API remains in-memory.
    Calling :meth:`save` or :meth:`load` binds it to a file; later mutations are
    then published atomically with a revision, integrity digest, three backup
    generations, and a legacy ``.bak`` compatibility copy.
    """

    def __init__(
        self,
        versions: Iterable[PriceVersion] = (),
        *,
        lock_timeout: float = 10.0,
    ):
        self._versions: dict[str, PriceVersion] = {}
        self._path: Optional[Path] = None
        self._lock = threading.RLock()
        self._revision = 0
        self._integrity: Optional[str] = None
        self._lock_timeout = max(0.0, float(lock_timeout))
        for version in versions:
            self._add_to_mapping(self._versions, version)

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def revision(self) -> int:
        return self._revision

    @classmethod
    def load_or_create(
        cls,
        path: Path | str,
        *,
        lock_timeout: float = 10.0,
    ) -> "PriceCatalog":
        """Load a catalog or create an empty revision-zero document."""

        target = cls._resolve_path(path)
        timeout = max(0.0, float(lock_timeout))
        lock = cls._file_lock(target, timeout)
        try:
            with lock:
                FileTransaction.recover_directory(target.parent)
                if target.is_file():
                    return cls._load_locked(target, timeout)
                if target.exists():
                    raise IsADirectoryError(str(target))
                catalog = cls((), lock_timeout=timeout)
                document = catalog._document((), revision=0)
                cls._persist_document(target, document, backup=False)
                catalog._path = target
                catalog._revision = 0
                catalog._integrity = str(document["integrity"])
                return catalog
        except FileLockTimeout as error:
            raise PriceCatalogLockError(f"price catalog lock timeout: {target}") from error

    def add(
        self,
        version: PriceVersion,
        *,
        expected_revision: Optional[int] = None,
    ) -> None:
        with self._lock:
            updated = dict(self._versions)
            self._add_to_mapping(updated, version)
            self._commit_versions(updated, expected_revision=expected_revision)

    def update(
        self,
        version: PriceVersion,
        *,
        expected_revision: Optional[int] = None,
    ) -> None:
        """Replace one version while preserving duplicate-time validation."""

        with self._lock:
            if version.version_id not in self._versions:
                raise KeyError(version.version_id)
            updated = dict(self._versions)
            del updated[version.version_id]
            self._add_to_mapping(updated, version)
            self._commit_versions(updated, expected_revision=expected_revision)

    def remove(
        self,
        version_id: str,
        *,
        expected_revision: Optional[int] = None,
    ) -> PriceVersion:
        """Remove and return a version, atomically when the catalog is bound."""

        with self._lock:
            if version_id not in self._versions:
                raise KeyError(version_id)
            updated = dict(self._versions)
            removed = updated.pop(version_id)
            self._commit_versions(updated, expected_revision=expected_revision)
            return removed

    def resolve(self, provider: str, model: str, requested_at: datetime) -> PriceVersion:
        requested_at = _as_utc(requested_at)
        with self._lock:
            matches = [
                version
                for version in self._versions.values()
                if version.provider == provider
                and version.model == model
                and version.effective_from <= requested_at
            ]
        if not matches:
            raise LookupError(f"no effective price for {provider}/{model}")
        return max(matches, key=lambda version: version.effective_from)

    def values(self) -> tuple[PriceVersion, ...]:
        with self._lock:
            return tuple(self._versions.values())

    def save(self, path: Path | str | None = None) -> None:
        """Atomically persist the current catalog and retain a previous backup."""

        with self._lock:
            target = self._bind_path(path)
            timeout = self._lock_timeout
            lock = self._file_lock(target, timeout)
            try:
                with lock:
                    FileTransaction.recover_directory(target.parent)
                    current = self._read_document(target) if target.is_file() else None
                    if self._path is not None and current is not None:
                        self._check_current(current, expected_revision=None)
                    revision = int(current.get("revision", 0)) if current else 0
                    document = self._document(
                        tuple(self._versions.values()), revision=revision
                    )
                    self._persist_document(target, document, backup=bool(current))
                    self._path = target
                    self._revision = revision
                    self._integrity = str(document["integrity"])
            except FileLockTimeout as error:
                raise PriceCatalogLockError(f"price catalog lock timeout: {target}") from error

    @classmethod
    def load(cls, path: Path | str) -> "PriceCatalog":
        """Load and validate a catalog document, retaining its persistence path."""

        target = cls._resolve_path(path)
        timeout = 10.0
        lock = cls._file_lock(target, timeout)
        try:
            with lock:
                FileTransaction.recover_directory(target.parent)
                return cls._load_locked(target, timeout)
        except FileLockTimeout as error:
            raise PriceCatalogLockError(f"price catalog lock timeout: {target}") from error

    def export_to(self, path: Path | str) -> None:
        """Export a validated catalog document without changing its binding."""

        with self._lock:
            target = self._resolve_path(path)
            document = self.export_document()
            lock = self._file_lock(target, self._lock_timeout)
            try:
                with lock:
                    FileTransaction.recover_directory(target.parent)
                    self._persist_document(target, document, backup=False)
            except FileLockTimeout as error:
                raise PriceCatalogLockError(f"price catalog lock timeout: {target}") from error

    def export_document(self) -> dict[str, object]:
        """Return a canonical, integrity-protected document for API export."""

        with self._lock:
            return self._document(
                tuple(self._versions.values()), revision=self._revision
            )

    def import_from(
        self,
        path: Path | str,
        *,
        expected_revision: Optional[int] = None,
    ) -> None:
        """Merge an exported document, rejecting duplicate or conflicting versions."""

        document = self._read_document(self._resolve_path(path))
        self.import_document(document, expected_revision=expected_revision)

    def import_document(
        self,
        payload: object,
        *,
        expected_revision: Optional[int] = None,
    ) -> int:
        """Validate and atomically merge a structured catalog document."""

        imported = self._parse_document(payload)[0]
        with self._lock:
            updated = dict(self._versions)
            for version in imported:
                self._add_to_mapping(updated, version)
            self._commit_versions(updated, expected_revision=expected_revision)
        return len(imported)

    def backup_generations(self) -> tuple[int, ...]:
        """Return available numbered backups without exposing host paths."""

        with self._lock:
            if self._path is None:
                return ()
            return tuple(
                generation
                for generation in range(1, PRICE_CATALOG_BACKUP_GENERATIONS + 1)
                if self._backup_path(self._path, generation).is_file()
            )

    def refresh(self) -> int:
        """Reload the bound catalog after another process publishes a revision.

        The document is fully parsed before any in-memory state is replaced, so
        a missing, malformed, or tampered file cannot partially refresh the
        current instance.
        """

        with self._lock:
            if self._path is None:
                raise ValueError("refresh requires a catalog path")
            target = self._path
            lock = self._file_lock(target, self._lock_timeout)
            try:
                with lock:
                    FileTransaction.recover_directory(target.parent)
                    document = self._read_document(target)
                    versions, revision = self._parse_document(document)
                    integrity = (
                        str(document["integrity"])
                        if document.get("integrity")
                        else None
                    )
                    self._versions = {
                        version.version_id: version for version in versions
                    }
                    self._revision = revision
                    self._integrity = integrity
                    return revision
            except FileLockTimeout as error:
                raise PriceCatalogLockError(
                    f"price catalog lock timeout: {target}"
                ) from error

    def restore_backup(
        self,
        path: Path | str | None = None,
        *,
        generation: Optional[int] = None,
        expected_revision: Optional[int] = None,
    ) -> None:
        """Restore a selected backup and publish it as the next revision."""

        with self._lock:
            target = self._bind_path(path)
            selected = self._select_backup(target, generation)
            lock = self._file_lock(target, self._lock_timeout)
            try:
                with lock:
                    FileTransaction.recover_directory(target.parent)
                    if not selected.is_file():
                        raise ValueError(f"price catalog backup does not exist: {selected}")
                    source = self._read_document(selected)
                    current = self._read_document(target) if target.is_file() else None
                    current_revision = int(current.get("revision", self._revision)) if current else self._revision
                    self._check_current(current, expected_revision=expected_revision)
                    new_revision = current_revision + 1
                    versions = self._parse_document(source)[0]
                    document = self._document(versions, revision=new_revision)
                    self._persist_document(target, document, backup=bool(current))
                    # A legacy .bak is a one-shot compatibility source when the
                    # catalog did not exist; leaving it behind would make a
                    # later restore appear to succeed twice from stale data.
                    if current is None and selected == self._legacy_backup_path(target):
                        self._remove_legacy_backup(target)
                    self._versions = {version.version_id: version for version in versions}
                    self._path = target
                    self._revision = new_revision
                    self._integrity = str(document["integrity"])
            except FileLockTimeout as error:
                raise PriceCatalogLockError(f"price catalog lock timeout: {target}") from error

    def _commit_versions(
        self,
        versions: dict[str, PriceVersion],
        *,
        expected_revision: Optional[int] = None,
    ) -> None:
        if self._path is None:
            if expected_revision is not None and expected_revision != self._revision:
                raise PriceCatalogConflictError(
                    f"price catalog revision conflict: expected {expected_revision}, current {self._revision}"
                )
            self._versions = versions
            return

        target = self._path
        lock = self._file_lock(target, self._lock_timeout)
        try:
            with lock:
                FileTransaction.recover_directory(target.parent)
                current = self._read_document(target)
                self._check_current(current, expected_revision=expected_revision)
                revision = int(current.get("revision", 0)) + 1
                document = self._document(tuple(versions.values()), revision=revision)
                self._persist_document(target, document, backup=True)
                self._versions = versions
                self._revision = revision
                self._integrity = str(document["integrity"])
        except FileLockTimeout as error:
            raise PriceCatalogLockError(f"price catalog lock timeout: {target}") from error

    def _bind_path(self, path: Path | str | None) -> Path:
        if path is None:
            if self._path is None:
                raise ValueError("a catalog path is required")
            return self._path
        return self._resolve_path(path)

    @staticmethod
    def _resolve_path(path: Path | str) -> Path:
        return Path(path).resolve()

    @staticmethod
    def _legacy_backup_path(path: Path) -> Path:
        return Path(f"{path}.bak")

    @classmethod
    def _backup_path(cls, path: Path, generation: int = 1) -> Path:
        if generation < 1 or generation > PRICE_CATALOG_BACKUP_GENERATIONS:
            raise ValueError("backup generation must be between 1 and 3")
        return Path(f"{path}.bak.{generation}")

    @staticmethod
    def _file_lock(path: Path, timeout: float) -> FileLock:
        return FileLock(str(Path(f"{path}.lock")), timeout=timeout)

    @classmethod
    def _load_locked(cls, target: Path, lock_timeout: float) -> "PriceCatalog":
        document = cls._read_document(target)
        versions, revision = cls._parse_document(document)
        catalog = cls(versions, lock_timeout=lock_timeout)
        catalog._path = target
        catalog._revision = revision
        catalog._integrity = str(document.get("integrity")) if document.get("integrity") else None
        return catalog

    def _check_current(
        self,
        current: Optional[dict[str, object]],
        *,
        expected_revision: Optional[int],
    ) -> None:
        if current is None:
            if expected_revision not in (None, 0):
                raise PriceCatalogConflictError(
                    f"price catalog revision conflict: expected {expected_revision}, current 0"
                )
            return
        current_revision = int(current.get("revision", 0))
        if expected_revision is not None and expected_revision != current_revision:
            raise PriceCatalogConflictError(
                f"price catalog revision conflict: expected {expected_revision}, current {current_revision}"
            )
        if self._path is not None and self._integrity is not None:
            current_integrity = current.get("integrity")
            if current_integrity != self._integrity:
                raise PriceCatalogConflictError(
                    "price catalog changed since it was loaded"
                )

    @classmethod
    def _select_backup(cls, target: Path, generation: Optional[int]) -> Path:
        if generation is not None:
            return cls._backup_path(target, generation)
        newest = cls._backup_path(target, 1)
        return newest if newest.is_file() else cls._legacy_backup_path(target)

    @classmethod
    def _remove_legacy_backup(cls, target: Path) -> None:
        try:
            cls._legacy_backup_path(target).unlink()
        except FileNotFoundError:
            pass

    @classmethod
    def _read_versions(cls, path: Path) -> tuple[PriceVersion, ...]:
        return cls._parse_document(cls._read_document(path))[0]

    @classmethod
    def _read_document(cls, path: Path) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"price catalog cannot be read: {path}") from error
        if not isinstance(payload, dict):
            raise ValueError("price catalog document is invalid")
        return payload

    @classmethod
    def _parse_document(cls, payload: object) -> tuple[tuple[PriceVersion, ...], int]:
        if not isinstance(payload, dict):
            raise ValueError("price catalog document is invalid")
        if payload.get("schema") != PRICE_CATALOG_SCHEMA:
            raise ValueError("price catalog schema is unsupported")
        if payload.get("schema_version") != PRICE_CATALOG_SCHEMA_VERSION:
            raise ValueError("price catalog schema version is unsupported")
        if payload.get("format_version") != PRICE_CATALOG_FORMAT_VERSION:
            raise ValueError("price catalog format version is unsupported")
        raw_versions = payload.get("versions")
        if not isinstance(raw_versions, list):
            raise ValueError("price catalog versions are invalid")
        raw_revision = payload.get("revision", 0)
        if isinstance(raw_revision, bool) or not isinstance(raw_revision, int) or raw_revision < 0:
            raise ValueError("price catalog revision is invalid")
        integrity = payload.get("integrity")
        if integrity is not None:
            if not isinstance(integrity, str) or integrity != cls.compute_integrity(payload):
                raise PriceCatalogIntegrityError("price catalog integrity check failed")

        versions: list[PriceVersion] = []
        seen: dict[str, PriceVersion] = {}
        for raw_version in raw_versions:
            if not isinstance(raw_version, dict):
                raise ValueError("price catalog version is invalid")
            try:
                version = PriceVersion.model_validate(raw_version)
            except ValueError as error:
                raise ValueError("price catalog version is invalid") from error
            cls._add_to_mapping(seen, version)
            versions.append(version)
        return tuple(versions), raw_revision

    @staticmethod
    def _add_to_mapping(
        versions: dict[str, PriceVersion], version: PriceVersion
    ) -> None:
        if version.version_id in versions:
            raise ValueError(f"duplicate price version: {version.version_id}")
        for existing in versions.values():
            if (
                existing.provider == version.provider
                and existing.model == version.model
                and existing.effective_from == version.effective_from
            ):
                raise ValueError(
                    "conflicting price version for "
                    f"{version.provider}/{version.model} at {version.effective_from.isoformat()}"
                )
        versions[version.version_id] = version

    @classmethod
    def _document(
        cls,
        versions: tuple[PriceVersion, ...],
        *,
        revision: int,
    ) -> dict[str, object]:
        document: dict[str, object] = {
            "schema": PRICE_CATALOG_SCHEMA,
            "schema_version": PRICE_CATALOG_SCHEMA_VERSION,
            "format_version": PRICE_CATALOG_FORMAT_VERSION,
            "revision": revision,
            "versions": [version.model_dump(mode="json") for version in versions],
        }
        document["integrity"] = cls.compute_integrity(document)
        return document

    @staticmethod
    def compute_integrity(payload: object) -> str:
        """Hash the canonical document excluding its self-referential digest."""

        if not isinstance(payload, dict):
            raise ValueError("price catalog document is invalid")
        canonical_payload = dict(payload)
        canonical_payload.pop("integrity", None)
        canonical = json.dumps(
            canonical_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @classmethod
    def _persist_document(
        cls, target: Path, document: dict[str, object], *, backup: bool
    ) -> None:
        mutations: list[FileMutation] = [
            FileMutation.replace(target, cls._json_writer(document)),
        ]
        if backup and target.is_file():
            # All sources are staged before any publication, so each copy sees
            # the same pre-transaction snapshot even when paths are rotated.
            mutations.extend(
                [
                    FileMutation.replace(cls._backup_path(target, 1), cls._copy_writer(target)),
                    FileMutation.replace(cls._legacy_backup_path(target), cls._copy_writer(target)),
                ]
            )
            for generation in (2, 3):
                previous = cls._backup_path(target, generation - 1)
                destination = cls._backup_path(target, generation)
                if previous.is_file():
                    mutations.append(FileMutation.replace(destination, cls._copy_writer(previous)))
                elif destination.is_file():
                    mutations.append(FileMutation.remove(destination))
        FileTransaction(target.parent, mutations).commit()

    @staticmethod
    def _json_writer(payload: dict[str, object]) -> Callable[[Path], None]:
        def writer(staged: Path) -> None:
            with staged.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n")

        return writer

    @staticmethod
    def _copy_writer(source: Path) -> Callable[[Path], None]:
        def writer(staged: Path) -> None:
            shutil.copyfile(source, staged)

        return writer


def calculate_cost_snapshot(
    usage: Usage,
    price: PriceVersion,
    *,
    requested_at: datetime,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> CostSnapshot:
    actual_provider = provider or price.provider
    actual_model = model or price.model
    if actual_provider != price.provider or actual_model != price.model:
        raise ValueError("price version does not match request provider or model")

    input_tokens = _input_tokens(usage)
    output_tokens = usage.completion_tokens
    cache_read_tokens = usage.cached_tokens
    cache_write_tokens = usage.cache_write_tokens

    input_cost = _cost(input_tokens, price.input_per_million)
    output_cost = _cost(output_tokens, price.output_per_million)
    cache_read_cost = _cost(cache_read_tokens, price.cache_read_per_million)
    cache_write_cost = _cost(cache_write_tokens, price.cache_write_per_million)
    dimensions = (input_cost, output_cost, cache_read_cost, cache_write_cost)
    total_cost = sum(dimensions, Decimal("0")) if all(value is not None for value in dimensions) else None

    return CostSnapshot(
        price_version_id=price.version_id,
        provider=price.provider,
        model=price.model,
        currency=price.currency,
        priced_at=_as_utc(requested_at),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        input_cost=input_cost,
        output_cost=output_cost,
        cache_read_cost=cache_read_cost,
        cache_write_cost=cache_write_cost,
        total_cost=total_cost,
        usage_source=usage.source,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value.astimezone(timezone.utc)


def _input_tokens(usage: Usage) -> Optional[int]:
    if usage.prompt_tokens is None:
        return None
    excluded = (usage.cached_tokens or 0) + (usage.cache_write_tokens or 0)
    return max(0, usage.prompt_tokens - excluded)


def _cost(tokens: Optional[int], rate: Decimal) -> Optional[Decimal]:
    if tokens is None:
        return None
    return (Decimal(tokens) * rate / Decimal("1000000")).quantize(Decimal("0.00000001"))
