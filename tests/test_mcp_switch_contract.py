import json

import pytest
from pydantic import ValidationError

from kirara_ai.config.global_config import MCPServerConfig
from kirara_ai.mcp_module.compat import (
    mcp_server_to_codex_toml,
    normalize_mcp_server_entry,
    parse_codex_toml,
    parse_mcp_json,
    project_mcp_server,
)


def test_canonical_entry_keeps_cc_switch_shape_and_argument_boundaries():
    entry = normalize_mcp_server_entry(
        {
            "id": "docs",
            "name": "Documentation server",
            "server": {
                "type": "stdio",
                "command": "node",
                "args": ["server.js", "--label", "a value"],
                "env": {"MODE": "read-only"},
                "cwd": "C:/workspace",
                "timeout_ms": 15_000,
            },
            "apps": {
                "claude": True,
                "codex": True,
                "gemini": False,
                "opencode": True,
            },
            "description": "Documentation tools",
            "tags": ["docs", "search"],
            "homepage": "https://example.invalid/docs",
            "docs": "https://example.invalid/docs/readme",
        }
    )

    assert entry.id == "docs"
    assert entry.name == "Documentation server"
    assert entry.server.type == "stdio"
    assert entry.server.args == ["server.js", "--label", "a value"]
    assert entry.server.model_extra["timeout_ms"] == 15_000
    assert entry.apps.claude is True
    assert entry.apps.codex is True

    dumped = entry.model_dump(by_alias=True, exclude_none=True)
    assert set(("id", "name", "server", "apps")).issubset(dumped)
    assert dumped["server"]["args"] == ["server.js", "--label", "a value"]
    assert "connection_type" not in dumped


@pytest.mark.parametrize(
    "server",
    [
        {"type": "stdio", "command": "node", "args": []},
        {"type": "http", "url": "https://example.invalid/mcp"},
        {"type": "sse", "url": "https://example.invalid/events"},
    ],
)
def test_all_cc_switch_transport_types_are_valid(server):
    entry = normalize_mcp_server_entry({"id": "server", "name": "Server", "server": server})
    assert entry.server.type == server["type"]


def test_args_must_be_an_array_and_are_never_split_or_joined():
    with pytest.raises(ValidationError):
        normalize_mcp_server_entry(
            {
                "id": "bad-args",
                "name": "Bad args",
                "server": {"type": "stdio", "command": "node", "args": "--label a value"},
            }
        )


def test_legacy_kirara_fields_are_migrated_without_becoming_the_public_shape():
    entry = MCPServerConfig.model_validate(
        {
            "id": "legacy",
            "description": "Old configuration",
            "connection_type": "sse",
            "url": "https://example.invalid/events",
            "enable": False,
        }
    )

    assert entry.server.type == "sse"
    assert entry.server.url == "https://example.invalid/events"
    assert entry.url == "https://example.invalid/events"
    assert entry.connection_type == "sse"
    assert entry.enable is False
    assert "runtime_enabled" in entry.metadata
    assert entry.metadata["runtime_enabled"] is False
    assert "connection_type" not in entry.model_dump(by_alias=True, exclude_none=True)


def test_json_import_accepts_canonical_map_and_common_mcp_servers_map():
    canonical = parse_mcp_json(
        json.dumps(
            {
                "docs": {
                    "id": "docs",
                    "name": "Docs",
                    "server": {"type": "http", "url": "https://example.invalid/mcp"},
                    "apps": {"codex": True},
                }
            }
        )
    )
    common = parse_mcp_json(
        json.dumps(
            {
                "mcpServers": {
                    "time": {"command": "node", "args": ["time.js"]},
                }
            }
        )
    )

    assert [item.id for item in canonical] == ["docs"]
    assert canonical[0].server.type == "http"
    assert [item.id for item in common] == ["time"]
    assert common[0].server.type == "stdio"
    assert common[0].server.args == ["time.js"]


def test_codex_toml_import_and_export_use_official_mcp_servers_shape():
    entry = parse_codex_toml(
        """
[mcp_servers.docs]
type = "http"
url = "https://example.invalid/mcp"
http_headers = { X-Trace = "example" }
timeout_ms = 20
"""
    )

    assert entry.id == "docs"
    assert entry.server.type == "http"
    assert entry.server.headers == {"X-Trace": "example"}
    assert entry.server.model_extra["timeout_ms"] == 20

    exported = mcp_server_to_codex_toml(entry)
    assert "[mcp_servers.docs]" in exported
    assert 'type = "http"' in exported
    assert 'url = "https://example.invalid/mcp"' in exported
    assert 'X-Trace = "example"' in exported
    assert "http_headers" in exported


def test_codex_toml_parser_accepts_legacy_nested_location_for_migration():
    entry = parse_codex_toml(
        """
[mcp.servers.legacy]
command = "python"
args = ["server.py", "a value"]
"""
    )

    assert entry.id == "legacy"
    assert entry.server.type == "stdio"
    assert entry.server.args == ["server.py", "a value"]


def test_client_projection_matches_cc_switch_field_names():
    entry = normalize_mcp_server_entry(
        {
            "id": "local",
            "name": "Local",
            "server": {
                "type": "stdio",
                "command": "node",
                "args": ["server.js", "a value"],
                "env": {"MODE": "read-only"},
                "cwd": "C:/workspace",
            },
        }
    )

    claude = project_mcp_server(entry, "claude")
    codex = project_mcp_server(entry, "codex")
    opencode = project_mcp_server(entry, "opencode")

    assert claude == codex | {}
    assert claude["args"] == ["server.js", "a value"]
    assert codex["env"] == {"MODE": "read-only"}
    assert opencode == {
        "type": "local",
        "command": ["node", "server.js", "a value"],
        "environment": {"MODE": "read-only"},
        "enabled": True,
    }


def test_remote_projection_uses_opencode_remote_and_codex_http_headers():
    entry = normalize_mcp_server_entry(
        {
            "id": "remote",
            "name": "Remote",
            "server": {
                "type": "sse",
                "url": "https://example.invalid/events",
                "headers": {"X-Trace": "example"},
            },
        }
    )

    assert project_mcp_server(entry, "codex") == {
        "type": "sse",
        "url": "https://example.invalid/events",
        "http_headers": {"X-Trace": "example"},
    }
    assert project_mcp_server(entry, "opencode") == {
        "type": "remote",
        "url": "https://example.invalid/events",
        "headers": {"X-Trace": "example"},
        "enabled": True,
    }
