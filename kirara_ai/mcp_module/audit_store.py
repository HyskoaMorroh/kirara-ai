"""Durable, redacted audit records for MCP runtime operations."""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any


_ERROR_TYPE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,127}$")
_ERROR_MESSAGE = "operation failed"


class MCPAuditStore:
    """Persist only the bounded public projection of an MCP audit event."""

    def __init__(self, database_path: str | Path, *, retention_limit: int = 1000):
        if retention_limit < 1:
            raise ValueError("MCP audit retention limit must be positive")
        self.database_path = Path(database_path).resolve()
        self.retention_limit = retention_limit
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_audit_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    component TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    server TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    outcome TEXT NOT NULL,
                    correlation_id TEXT,
                    error_type TEXT,
                    error_message TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_mcp_audit_filter
                ON mcp_audit_records (server, operation, outcome, id DESC)
                """
            )

    @staticmethod
    def _required_text(record: Mapping[str, Any], key: str, *, maximum: int) -> str:
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"MCP audit {key} must be a non-empty string")
        return value.strip()[:maximum]

    @staticmethod
    def _optional_text(value: Any, *, maximum: int) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        return value.strip()[:maximum]

    @staticmethod
    def _error_type(error: Any) -> str | None:
        if not isinstance(error, Mapping):
            return None
        value = error.get("type")
        if isinstance(value, str) and _ERROR_TYPE_PATTERN.fullmatch(value.strip()):
            return value.strip()
        return "MCPError"

    def append(self, record: Mapping[str, Any]) -> None:
        """Append a redacted event and enforce the configured retention bound."""

        component = self._required_text(record, "component", maximum=32)
        timestamp = self._required_text(record, "timestamp", maximum=64)
        server = self._required_text(record, "server", maximum=256)
        operation = self._required_text(record, "operation", maximum=128)
        outcome = self._required_text(record, "outcome", maximum=128)
        duration = record.get("duration_ms")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            raise ValueError("MCP audit duration_ms must be numeric")
        error_type = self._error_type(record.get("error"))

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO mcp_audit_records (
                    component, timestamp, server, operation, duration_ms,
                    outcome, correlation_id, error_type, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    component,
                    timestamp,
                    server,
                    operation,
                    float(duration),
                    outcome,
                    self._optional_text(record.get("correlation_id"), maximum=256),
                    error_type,
                    _ERROR_MESSAGE if error_type is not None else None,
                ),
            )
            connection.execute(
                """
                DELETE FROM mcp_audit_records
                WHERE id NOT IN (
                    SELECT id FROM mcp_audit_records
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (self.retention_limit,),
            )

    def list(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        server_id: str | None = None,
        operation: str | None = None,
        outcome: str | None = None,
    ) -> dict[str, object]:
        if offset < 0:
            raise ValueError("audit offset cannot be negative")
        if limit < 1 or limit > 200:
            raise ValueError("audit limit is outside the allowed range")

        filters: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("server", server_id),
            ("operation", operation),
            ("outcome", outcome),
        ):
            normalized = value.strip() if value else None
            if normalized:
                filters.append(f"{column} = ?")
                parameters.append(normalized)
        where_clause = f" WHERE {' AND '.join(filters)}" if filters else ""

        with self._lock, self._connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM mcp_audit_records{where_clause}",
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT component, timestamp, server, operation, duration_ms,
                       outcome, correlation_id, error_type, error_message
                FROM mcp_audit_records
                {where_clause}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()

        items = []
        for row in rows:
            items.append(
                {
                    "component": row["component"],
                    "timestamp": row["timestamp"],
                    "server": row["server"],
                    "operation": row["operation"],
                    "duration_ms": row["duration_ms"],
                    "outcome": row["outcome"],
                    "correlation_id": row["correlation_id"],
                    "error": (
                        {
                            "type": row["error_type"],
                            "message": row["error_message"],
                        }
                        if row["error_type"] is not None
                        else None
                    ),
                }
            )
        return {
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(items) < total,
            "persistent": True,
            "retention_limit": self.retention_limit,
        }
