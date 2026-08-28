"""Durable one-time confirmation records for management MCP tool calls."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path

from kirara_ai.web.auth.principal import get_runtime_principal


class MCPConfirmationStore:
    """Persist confirmation state with an atomic, cross-process consume step."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_tool_confirmations (
                    token_digest TEXT PRIMARY KEY,
                    subject_digest TEXT,
                    agent_id TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    params_digest TEXT NOT NULL,
                    tool_digest TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(mcp_tool_confirmations)"
                )
            }
            if "subject_digest" not in columns:
                connection.execute(
                    "ALTER TABLE mcp_tool_confirmations ADD COLUMN subject_digest TEXT"
                )
            connection.execute(
                "DELETE FROM mcp_tool_confirmations WHERE expires_at <= ?",
                (time.time(),),
            )

    def issue(
        self,
        token: str,
        *,
        subject_digest: str | None = None,
        agent_id: str,
        server_id: str,
        tool_name: str,
        params_digest: str,
        tool_digest: str,
        expires_at: float,
    ) -> None:
        resolved_subject_digest = self._subject_digest(
            subject_digest,
            required=True,
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO mcp_tool_confirmations (
                    token_digest, subject_digest, agent_id, server_id, tool_name,
                    params_digest, tool_digest, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._digest(token),
                    resolved_subject_digest,
                    agent_id,
                    server_id,
                    tool_name,
                    params_digest,
                    tool_digest,
                    expires_at,
                ),
            )

    def consume(
        self,
        token: str,
        *,
        subject_digest: str | None = None,
        agent_id: str,
        server_id: str,
        tool_name: str,
        params_digest: str,
        tool_digest: str,
        now: float | None = None,
    ) -> bool:
        """Consume only an exact, live record; concurrent callers race safely."""
        current_time = time.time() if now is None else now
        resolved_subject_digest = self._subject_digest(
            subject_digest,
            required=False,
        )
        if resolved_subject_digest is None:
            return False
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM mcp_tool_confirmations WHERE expires_at <= ?",
                (current_time,),
            )
            cursor = connection.execute(
                """
                DELETE FROM mcp_tool_confirmations
                WHERE token_digest = ?
                  AND subject_digest = ?
                  AND agent_id = ?
                  AND server_id = ?
                  AND tool_name = ?
                  AND params_digest = ?
                  AND tool_digest = ?
                  AND expires_at > ?
                """,
                (
                    self._digest(token),
                    resolved_subject_digest,
                    agent_id,
                    server_id,
                    tool_name,
                    params_digest,
                    tool_digest,
                    current_time,
                ),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _subject_digest(value: str | None, *, required: bool) -> str | None:
        if value is None:
            principal = get_runtime_principal()
            if principal is None:
                if required:
                    raise PermissionError("confirmation principal is required")
                return None
            value = principal.subject_digest
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("confirmation subject digest is invalid")
        return value
