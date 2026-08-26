"""Durable, server-owned storage for Agent conversation state."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from kirara_ai.llm.format.message import LLMChatMessage


_STORE_FORMAT_VERSION = 1
_CONFIRMATION_STATES = frozenset(
    {
        "awaiting_confirmation",
        "executing",
        "succeeded",
        "failed",
        "expired",
    }
)
_ACTIVE_CONFIRMATION_STATES = frozenset({"awaiting_confirmation", "executing"})
_PROCESS_LOCKS: dict[Path, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class SessionStore:
    """Persist conversation history and confirmation state atomically."""

    def __init__(
        self,
        data_path: str | Path,
        *,
        confirmation_ttl_seconds: int = 900,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if confirmation_ttl_seconds <= 0:
            raise ValueError("confirmation TTL must be positive")
        self.root = Path(data_path).resolve() / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.pending_path = self.root / "pending.json"
        self._lock_path = self.root / ".store.lock"
        self.confirmation_ttl_seconds = confirmation_ttl_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        with _PROCESS_LOCKS_GUARD:
            self._process_lock = _PROCESS_LOCKS.setdefault(
                self._lock_path, threading.RLock()
            )

    @staticmethod
    def _session_digest(session_key: str) -> str:
        return hashlib.sha256(session_key.encode("utf-8")).hexdigest()

    def _history_path(
        self,
        session_key: str,
        agent_id: str | None = None,
    ) -> Path:
        if agent_id is None:
            digest_source = session_key
        else:
            digest_source = json.dumps(
                [session_key, str(agent_id)],
                ensure_ascii=True,
                separators=(",", ":"),
            )
        return self.root / f"{self._session_digest(digest_source)}.json"

    def save_history(
        self,
        session_key: str,
        messages: Iterable[LLMChatMessage],
        *,
        agent_id: str | None = None,
    ) -> None:
        payload = {
            "format_version": _STORE_FORMAT_VERSION,
            "messages": [self._serialize_message(message) for message in messages],
        }
        if agent_id is not None:
            payload["agent_id"] = str(agent_id)
        with self._transaction():
            self._atomic_write(
                self._history_path(session_key, agent_id=agent_id),
                payload,
            )

    def load_history(
        self,
        session_key: str,
        *,
        agent_id: str | None = None,
    ) -> list[LLMChatMessage]:
        with self._transaction():
            payload = self._read_json(
                self._history_path(session_key, agent_id=agent_id),
                default=None,
            )
            if payload is None and agent_id is not None:
                payload = self._read_json(
                    self._history_path(session_key),
                    default=None,
                )
        if payload is None:
            return []
        if (
            not isinstance(payload, dict)
            or payload.get("format_version") != _STORE_FORMAT_VERSION
            or not isinstance(payload.get("messages"), list)
        ):
            raise ValueError("session history format is invalid")
        stored_agent_id = payload.get("agent_id")
        if (
            agent_id is not None
            and (
                stored_agent_id is None
                or str(stored_agent_id) != str(agent_id)
            )
        ):
            return []
        try:
            return [LLMChatMessage.model_validate(item) for item in payload["messages"]]
        except (TypeError, ValueError) as error:
            raise ValueError("session history contains an invalid message") from error

    def save_pending(self, record: dict[str, Any], *, session_key: str) -> None:
        confirmation_id = record.get("confirmation_id")
        if not isinstance(confirmation_id, str) or not confirmation_id:
            raise ValueError("pending confirmation ID is required")
        now = self._now()
        persisted = dict(record)
        persisted.pop("context", None)
        persisted.update(
            {
                "confirmation_id": confirmation_id,
                "session_digest": self._session_digest(session_key),
                "status": "awaiting_confirmation",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "expires_at": (
                    now + timedelta(seconds=self.confirmation_ttl_seconds)
                ).isoformat(),
            }
        )
        with self._transaction():
            records = self._load_confirmation_payload()
            if confirmation_id in records:
                raise ValueError("pending confirmation ID already exists")
            records[confirmation_id] = self._validate_confirmation(persisted)
            self._write_confirmations(records)

    def claim_pending(
        self, confirmation_id: str, session_key: str
    ) -> tuple[str, dict[str, Any] | None]:
        """Atomically claim a confirmation and return its claim outcome."""

        with self._transaction():
            records = self._load_confirmation_payload()
            record = records.get(confirmation_id)
            if record is None:
                return "not_found", None
            if not record.get("correlation_id"):
                record = dict(record)
                record["correlation_id"] = uuid.uuid4().hex
                records[confirmation_id] = record
                self._write_confirmations(records)
            expected_digest = self._session_digest(session_key)
            if not hmac.compare_digest(record["session_digest"], expected_digest):
                return "session_mismatch", dict(record)
            status = record["status"]
            if status == "awaiting_confirmation" and self._is_expired(record):
                record = self._transition(
                    record,
                    "expired",
                    error_type="ConfirmationExpired",
                )
                records[confirmation_id] = record
                self._write_confirmations(records)
                return "expired", dict(record)
            if status != "awaiting_confirmation":
                return status, dict(record)
            record = self._transition(record, "executing")
            records[confirmation_id] = record
            self._write_confirmations(records)
            return "executing", dict(record)

    def complete_pending(
        self,
        confirmation_id: str,
        status: str,
        *,
        error_type: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"succeeded", "failed", "expired"}:
            raise ValueError("confirmation terminal status is invalid")
        with self._transaction():
            records = self._load_confirmation_payload()
            record = records.get(confirmation_id)
            if record is None:
                raise LookupError("confirmation is missing")
            if record["status"] == status:
                return dict(record)
            if record["status"] != "executing":
                raise ValueError("confirmation is not executing")
            record = self._transition(record, status, error_type=error_type)
            records[confirmation_id] = record
            self._write_confirmations(records)
            return dict(record)

    def get_confirmation(self, confirmation_id: str) -> dict[str, Any] | None:
        with self._transaction():
            record = self._load_confirmation_payload().get(confirmation_id)
            return dict(record) if record is not None else None

    def load_confirmations(self) -> list[dict[str, Any]]:
        with self._transaction():
            records = self._load_confirmation_payload()
            return [dict(records[key]) for key in sorted(records)]

    def load_pending(self) -> list[dict[str, Any]]:
        return [
            record
            for record in self.load_confirmations()
            if record["status"] in _ACTIVE_CONFIRMATION_STATES
        ]

    def delete_pending(self, confirmation_id: str) -> None:
        """Compatibility cleanup for active records only."""

        with self._transaction():
            records = self._load_confirmation_payload()
            record = records.get(confirmation_id)
            if record is None or record["status"] not in _ACTIVE_CONFIRMATION_STATES:
                return
            records.pop(confirmation_id)
            self._write_confirmations(records)

    def _transition(
        self,
        record: dict[str, Any],
        status: str,
        *,
        error_type: str | None = None,
    ) -> dict[str, Any]:
        result = dict(record)
        result["status"] = status
        result["updated_at"] = self._now().isoformat()
        result.pop("error_type", None)
        if error_type:
            result["error_type"] = str(error_type)[:128]
        return self._validate_confirmation(result)

    def _is_expired(self, record: dict[str, Any]) -> bool:
        return self._parse_datetime(record["expires_at"]) <= self._now()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise TypeError("session store clock must return datetime")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _serialize_message(message: LLMChatMessage) -> dict[str, Any]:
        if not isinstance(message, LLMChatMessage):
            raise TypeError("session history accepts LLMChatMessage values only")
        return message.model_dump(mode="json")

    def _load_confirmation_payload(self) -> dict[str, dict[str, Any]]:
        payload = self._read_json(self.pending_path, default=None)
        if payload is None:
            return {}
        if (
            not isinstance(payload, dict)
            or payload.get("format_version") != _STORE_FORMAT_VERSION
            or not isinstance(payload.get("items"), list)
        ):
            raise ValueError("pending confirmation store format is invalid")
        result: dict[str, dict[str, Any]] = {}
        for raw_record in payload["items"]:
            record = self._validate_confirmation(
                self._upgrade_confirmation(raw_record)
            )
            confirmation_id = record["confirmation_id"]
            if confirmation_id in result:
                raise ValueError("pending confirmation record is duplicated")
            result[confirmation_id] = record
        return result

    def _upgrade_confirmation(self, raw_record: Any) -> dict[str, Any]:
        if not isinstance(raw_record, dict):
            raise ValueError("pending confirmation record is invalid")
        record = dict(raw_record)
        if "status" in record:
            return record
        context = record.pop("context", None)
        if not isinstance(context, dict):
            raise ValueError("pending confirmation record is invalid")
        fields = (
            "channel_type",
            "adapter_instance",
            "account_scope",
            "conversation_scope",
            "sender_scope",
        )
        if any(not isinstance(context.get(field), str) for field in fields):
            raise ValueError("pending confirmation record is invalid")
        now = self._now()
        record.update(
            {
                "session_digest": self._session_digest(
                    "/".join(context[field] for field in fields)
                ),
                "status": "awaiting_confirmation",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "expires_at": (
                    now + timedelta(seconds=self.confirmation_ttl_seconds)
                ).isoformat(),
            }
        )
        return record

    @classmethod
    def _validate_confirmation(cls, record: dict[str, Any]) -> dict[str, Any]:
        confirmation_id = record.get("confirmation_id")
        session_digest = record.get("session_digest")
        status = record.get("status")
        if not isinstance(confirmation_id, str) or not confirmation_id:
            raise ValueError("pending confirmation record is invalid")
        if (
            not isinstance(session_digest, str)
            or len(session_digest) != 64
            or any(character not in "0123456789abcdef" for character in session_digest)
        ):
            raise ValueError("pending confirmation record is invalid")
        if status not in _CONFIRMATION_STATES:
            raise ValueError("pending confirmation record is invalid")
        correlation_id = record.get("correlation_id")
        if correlation_id is not None and (
            not isinstance(correlation_id, str)
            or not correlation_id
            or len(correlation_id) > 64
        ):
            raise ValueError("pending confirmation record is invalid")
        for field in ("created_at", "updated_at", "expires_at"):
            value = record.get(field)
            if not isinstance(value, str):
                raise ValueError("pending confirmation record is invalid")
            cls._parse_datetime(value)
        return dict(record)

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise ValueError("pending confirmation record is invalid") from error
        if parsed.tzinfo is None:
            raise ValueError("pending confirmation record is invalid")
        return parsed.astimezone(timezone.utc)

    def _write_confirmations(self, records: dict[str, dict[str, Any]]) -> None:
        self._atomic_write(
            self.pending_path,
            {
                "format_version": _STORE_FORMAT_VERSION,
                "items": [records[key] for key in sorted(records)],
            },
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Serialize read-modify-write operations across instances and processes."""

        with self._process_lock:
            handle = self._lock_path.open("a+b")
            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    @staticmethod
    def _read_json(path: Path, *, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("session store cannot be read") from error

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
