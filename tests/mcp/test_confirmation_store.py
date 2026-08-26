from pathlib import Path

from kirara_ai.mcp_module.confirmation_store import MCPConfirmationStore


_NOW = 2_000_000_000.0


def _issue(store: MCPConfirmationStore, token: str, expires_at: float = _NOW + 100.0) -> None:
    store.issue(
        token,
        agent_id="agent",
        server_id="server",
        tool_name="server.search",
        params_digest="params",
        tool_digest="tool-v1",
        expires_at=expires_at,
    )


def _consume(store: MCPConfirmationStore, token: str, **overrides) -> bool:
    values = {
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
