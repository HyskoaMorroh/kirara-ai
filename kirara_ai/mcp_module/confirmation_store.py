"""Durable one-time confirmation records for management MCP tool calls."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path


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
                    agent_id TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    params_digest TEXT NOT NULL,
                    tool_digest TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "DELETE FROM mcp_tool_confirmations WHERE expires_at <= ?",
                (time.time(),),
            )

    def issue(
        self,
        token: str,
        *,
        agent_id: str,
        server_id: str,
        tool_name: str,
        params_digest: str,
        tool_digest: str,
        expires_at: float,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO mcp_tool_confirmations (
                    token_digest, agent_id, server_id, tool_name,
                    params_digest, tool_digest, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._digest(token),
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
        agent_id: str,
        server_id: str,
        tool_name: str,
        params_digest: str,
        tool_digest: str,
        now: float | None = None,
    ) -> bool:
        """Consume only an exact, live record; concurrent callers race safely."""
        current_time = time.time() if now is None else now
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
                  AND agent_id = ?
                  AND server_id = ?
                  AND tool_name = ?
                  AND params_digest = ?
                  AND tool_digest = ?
                  AND expires_at > ?
                """,
                (
                    self._digest(token),
                    agent_id,
                    server_id,
                    tool_name,
                    params_digest,
                    tool_digest,
                    current_time,
                ),
            )
            return cursor.rowcount == 1
