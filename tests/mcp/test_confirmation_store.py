import sqlite3
from pathlib import Path

import pytest

from kirara_ai.mcp_module.confirmation_store import MCPConfirmationStore
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context


_NOW = 2_000_000_000.0
_CREATOR = RuntimePrincipal(subject="creator", is_creator=True)


def _issue(store: MCPConfirmationStore, token: str, expires_at: float = _NOW + 100.0) -> None:
    store.issue(
        token,
        subject_digest="a" * 64,
        agent_id="agent",
        server_id="server",
        tool_name="server.search",
        params_digest="params",
        tool_digest="tool-v1",
        expires_at=expires_at,
    )


def _consume(store: MCPConfirmationStore, token: str, **overrides) -> bool:
    values = {
        "subject_digest": "a" * 64,
        "agent_id": "agent",
        "server_id": "server",
        "tool_name": "server.search",
        "params_digest": "params",
        "tool_digest": "tool-v1",
        "now": _NOW,
    }
    values.update(overrides)
    return store.consume(token, **values)


def test_confirmation_is_one_time_and_survives_new_store_instance(tmp_path: Path):
    database = tmp_path / "mcp" / "confirmations.db"
    _issue(MCPConfirmationStore(database), "one-time")

    restarted_store = MCPConfirmationStore(database)
    assert _consume(restarted_store, "one-time") is True
    assert _consume(MCPConfirmationStore(database), "one-time") is False


def test_confirmation_rejects_expiry_and_context_changes(tmp_path: Path):
    store = MCPConfirmationStore(tmp_path / "confirmations.db")
    _issue(store, "expired", expires_at=_NOW - 1)
    assert _consume(store, "expired") is False

    _issue(store, "wrong-agent")
    assert _consume(store, "wrong-agent", agent_id="other") is False
    assert _consume(store, "wrong-agent") is True

    _issue(store, "changed-tool")
    assert _consume(store, "changed-tool", tool_digest="tool-v2") is False
    assert _consume(store, "changed-tool") is True

    _issue(store, "changed-subject")
    assert _consume(store, "changed-subject", subject_digest="b" * 64) is False
    assert _consume(store, "changed-subject") is True


def test_confirmation_store_migrates_old_schema_and_legacy_rows_fail_closed(tmp_path: Path):
    database = tmp_path / "confirmations.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE mcp_tool_confirmations (
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
            """
            INSERT INTO mcp_tool_confirmations (
                token_digest, agent_id, server_id, tool_name,
                params_digest, tool_digest, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                MCPConfirmationStore._digest("legacy"),
                "agent",
                "server",
                "server.search",
                "params",
                "tool-v1",
                _NOW + 100,
            ),
        )

    store = MCPConfirmationStore(database)

    assert _consume(store, "legacy") is False
    _issue(store, "new")
    assert _consume(store, "new") is True


def test_confirmation_store_derives_subject_digest_from_runtime_principal(tmp_path: Path):
    store = MCPConfirmationStore(tmp_path / "confirmations.db")

    with pytest.raises(PermissionError):
        store.issue(
            "anonymous",
            agent_id="agent",
            server_id="server",
            tool_name="server.search",
            params_digest="params",
            tool_digest="tool-v1",
            expires_at=_NOW + 100,
        )

    with runtime_principal_context(_CREATOR):
        store.issue(
            "creator",
            agent_id="agent",
            server_id="server",
            tool_name="server.search",
            params_digest="params",
            tool_digest="tool-v1",
            expires_at=_NOW + 100,
        )
        assert store.consume(
            "creator",
            agent_id="agent",
            server_id="server",
            tool_name="server.search",
            params_digest="params",
            tool_digest="tool-v1",
            now=_NOW,
        ) is True
