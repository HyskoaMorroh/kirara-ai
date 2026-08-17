from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TextIO


StageWriter = Callable[[Path], None]
TextWriter = Callable[[TextIO], None]
AfterPublish = Callable[[], None]


@dataclass(frozen=True)
class FileMutation:
    """One file replacement or deletion in a recoverable logical write."""

    target: Path | str
    stage_writer: Optional[StageWriter] = field(default=None, repr=False)
    delete: bool = False

    def __post_init__(self) -> None:
        if self.delete == (self.stage_writer is not None):
            raise ValueError("A mutation must either write or delete one file")

    @classmethod
    def replace(cls, target: Path | str, writer: StageWriter) -> "FileMutation":
        return cls(target=target, stage_writer=writer)

    @classmethod
    def remove(cls, target: Path | str) -> "FileMutation":
        return cls(target=target, delete=True)


class FileTransaction:
    """Publish a group of file changes with rollback and startup recovery.

    Staged files and backups live beside their targets so each final replace is
    atomic on the target filesystem. The journal lives in ``directory`` and is
    durable before publication begins. A handled error rolls back to the old
    state; recovery after process interruption completes the prepared new state.
    """

    JOURNAL_VERSION = 1
    JOURNAL_PREFIX = ".kirara-transaction-"
    JOURNAL_SUFFIX = ".json"

    def __init__(
        self, directory: Path | str, mutations: list[FileMutation] | tuple[FileMutation, ...]
    ) -> None:
        self.directory = Path(directory).resolve()
        self.mutations = tuple(mutations)
        if not self.mutations:
            raise ValueError("A file transaction requires at least one mutation")

    def commit(self, after_publish: Optional[AfterPublish] = None) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        transaction_id = uuid.uuid4().hex
        entries = self._prepare_entries(transaction_id)
        journal_path = self.directory / (
            f"{self.JOURNAL_PREFIX}{transaction_id}{self.JOURNAL_SUFFIX}"
        )
        journal = {
            "version": self.JOURNAL_VERSION,
            "transaction_id": transaction_id,
            "state": "prepared",
            "entries": entries,
        }

        try:
            self._write_journal(journal_path, journal)
        except BaseException:
            # The journal is not durable yet, so no recovery agent can own
            # these preparation artifacts. Remove them before surfacing the
            # original failure.
            self._cleanup_entries(entries)
            self._unlink(journal_path)
            self._fsync_directory(self.directory)
            raise
        try:
            for entry in entries:
                entry["state"] = "publishing"
                self._write_journal(journal_path, journal)
                self._publish_entry(entry)
                entry["state"] = "published"
                self._write_journal(journal_path, journal)

            if after_publish is not None:
                after_publish()

            journal["state"] = "committed"
            self._write_journal(journal_path, journal)
        except Exception:
            # A durable rollback marker is required before consuming backups.
            # If writing it fails, leave the prepared transaction intact so
            # startup recovery can still finish the complete new state.
            journal["state"] = "rolling_back"
            try:
                self._write_journal(journal_path, journal)
            except Exception:
                raise
            if self._rollback_entries(entries):
                self._cleanup_entries(entries)
                self._unlink(journal_path)
                self._fsync_directory(self.directory)
            raise

        # Once the committed marker is durable, cleanup is retryable and must
        # not turn a successful logical write into an API failure.
        try:
            self._cleanup_entries(entries)
            self._unlink(journal_path)
            self._fsync_directory(self.directory)
        except OSError:
            pass

    @classmethod
    def recover_directory(cls, directory: Path | str) -> None:
        root = Path(directory).resolve()
        if not root.is_dir():
            return

        pattern = f"{cls.JOURNAL_PREFIX}*{cls.JOURNAL_SUFFIX}"
        for journal_path in sorted(root.glob(pattern)):
            journal = cls._read_journal(journal_path)
            entries = journal["entries"]
            cls._validate_recovery_entries(root, entries)

            if journal.get("state") == "rolling_back":
                if not cls._rollback_entries(entries):
                    raise OSError(f"Failed to roll back file transaction {journal_path}")
            elif journal.get("state") != "committed":
                for entry in entries:
                    entry["state"] = "publishing"
                    cls._write_journal(journal_path, journal)
                    cls._publish_entry(entry)
                    entry["state"] = "published"
                    cls._write_journal(journal_path, journal)
                journal["state"] = "committed"
                cls._write_journal(journal_path, journal)

            cls._cleanup_entries(entries)
            cls._unlink(journal_path)
            cls._fsync_directory(root)

    def _prepare_entries(self, transaction_id: str) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        seen_targets: set[Path] = set()
        try:
            for index, mutation in enumerate(self.mutations):
                target = Path(mutation.target).resolve()
                self._ensure_within_directory(target)
                if target in seen_targets:
                    raise ValueError(f"Duplicate transaction target: {target}")
                seen_targets.add(target)
                target.parent.mkdir(parents=True, exist_ok=True)

                suffix = f".{transaction_id}.{index}"
                staged = target.parent / f".{target.name}{suffix}.staged"
                backup = target.parent / f".{target.name}{suffix}.backup"
                had_target = target.is_file()
                if target.exists() and not had_target:
                    raise IsADirectoryError(str(target))

                entry: dict[str, object] = {
                    "target": str(target),
                    "staged": None if mutation.delete else str(staged),
                    "backup": str(backup),
                    "delete": mutation.delete,
                    "had_target": had_target,
                    "state": "pending",
                }
                # Add the entry before any file is created so a writer failure
                # also cleans the current mutation's backup and staged file.
                entries.append(entry)

                if had_target:
                    shutil.copyfile(target, backup)
                    self._fsync_file(backup)

                if not mutation.delete:
                    assert mutation.stage_writer is not None
                    mutation.stage_writer(staged)
                    if not staged.is_file():
                        raise OSError(f"Stage writer did not create {staged}")
                    self._fsync_file(staged)

                self._fsync_directory(target.parent)
        except BaseException:
            self._cleanup_entries(entries)
            raise
        return entries

    def _ensure_within_directory(self, target: Path) -> None:
        try:
            common = Path(os.path.commonpath((str(target), str(self.directory))))
        except ValueError as error:
            raise ValueError(f"Transaction target is outside {self.directory}") from error
        if common != self.directory:
            raise ValueError(f"Transaction target is outside {self.directory}: {target}")

    @staticmethod
    def _publish_entry(entry: dict[str, object]) -> None:
        target = Path(str(entry["target"]))
        if bool(entry["delete"]):
            if target.exists():
                target.unlink()
        else:
            staged_value = entry.get("staged")
            staged = Path(str(staged_value)) if staged_value else None
            if staged is not None and staged.exists():
                os.replace(staged, target)
            elif entry.get("state") not in {"publishing", "published"}:
                raise OSError(f"Cannot recover missing staged file for {target}")
            elif not target.is_file():
                raise OSError(f"Cannot recover missing staged file for {target}")
        FileTransaction._fsync_directory(target.parent)

    @staticmethod
    def _rollback_entries(entries: list[dict[str, object]]) -> bool:
        try:
            for entry in reversed(entries):
                target = Path(str(entry["target"]))
                backup = Path(str(entry["backup"]))
                if bool(entry["had_target"]):
                    if backup.exists():
                        os.replace(backup, target)
                    elif not target.is_file():
                        raise OSError(f"Cannot recover missing backup for {target}")
                elif target.exists():
                    target.unlink()
                FileTransaction._fsync_directory(target.parent)
            return True
        except OSError:
            return False

    @staticmethod
    def _cleanup_entries(entries: list[dict[str, object]]) -> None:
        for entry in entries:
            for key in ("staged", "backup"):
                value = entry.get(key)
                if value:
                    FileTransaction._unlink(Path(str(value)))

    @classmethod
    def _validate_recovery_entries(
        cls, root: Path, entries: object
    ) -> None:
        if not isinstance(entries, list) or not entries:
            raise ValueError("Invalid file transaction journal entries")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid file transaction journal entry")
            for key in ("target", "backup"):
                value = entry.get(key)
                if not isinstance(value, str):
                    raise ValueError(f"Invalid transaction {key}")
                path = Path(value).resolve()
                try:
                    common = Path(os.path.commonpath((str(path), str(root))))
                except ValueError as error:
                    raise ValueError(
                        f"Transaction {key} is outside recovery root"
                    ) from error
                if common != root:
                    raise ValueError(f"Transaction {key} is outside recovery root")
            staged = entry.get("staged")
            if staged is not None:
                if not isinstance(staged, str):
                    raise ValueError("Invalid transaction staged path")
                staged_path = Path(str(staged)).resolve()
                try:
                    common = Path(
                        os.path.commonpath((str(staged_path), str(root)))
                    )
                except ValueError as error:
                    raise ValueError(
                        "Transaction staged path is outside recovery root"
                    ) from error
                if common != root:
                    raise ValueError("Transaction staged path is outside recovery root")
            if not isinstance(entry.get("delete"), bool) or not isinstance(
                entry.get("had_target"), bool
            ):
                raise ValueError("Invalid transaction operation flags")

    @classmethod
    def _read_journal(cls, journal_path: Path) -> dict[str, object]:
        with journal_path.open("r", encoding="utf-8") as file:
            journal = json.load(file)
        if not isinstance(journal, dict) or journal.get("version") != cls.JOURNAL_VERSION:
            raise ValueError(f"Unsupported file transaction journal: {journal_path}")
        return journal

    @staticmethod
    def _write_journal(journal_path: Path, journal: dict[str, object]) -> None:
        fd, temp_name = tempfile.mkstemp(
            dir=journal_path.parent,
            prefix=f".{journal_path.name}.",
            suffix=".tmp",
            text=True,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(journal, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, journal_path)
            FileTransaction._fsync_directory(journal_path.parent)
        except BaseException:
            FileTransaction._unlink(temp_path)
            raise

    @staticmethod
    def _fsync_file(path: Path) -> None:
        # Windows rejects fsync on a read-only descriptor with EBADF even when
        # the underlying file is valid. Reopen without truncation but writable.
        with path.open("r+b") as file:
            os.fsync(file.fileno())

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def atomic_write_text(path: Path | str, writer: TextWriter) -> None:
    """Atomically write one UTF-8 text file through the transaction boundary."""

    target = Path(path).resolve()

    def stage(staged_path: Path) -> None:
        with staged_path.open("w", encoding="utf-8") as file:
            writer(file)
            file.flush()
            os.fsync(file.fileno())

    FileTransaction(target.parent, [FileMutation.replace(target, stage)]).commit()
