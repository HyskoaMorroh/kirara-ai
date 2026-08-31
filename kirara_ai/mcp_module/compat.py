"""MCP configuration boundaries for the interoperable entry shape.

The canonical Kirara representation intentionally matches the public entry
shape used by mainstream MCP desktop managers.  This module owns format
conversion at import/export boundaries so the runtime and API do not need to
understand several competing schemas.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Dict, Iterable, List

import tomli

from kirara_ai.config.global_config import MCPServerConfig, MCPTransportConfig


SUPPORTED_TYPES = {"stdio", "http", "sse"}
APP_KEYS = (
    "claude",
    "claude-desktop",
    "codex",
    "gemini",
    "grokbuild",
    "opencode",
    "openclaw",
    "hermes",
)


def _string_map(value: Any, field_name: str) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"MCP {field_name} must be an object")
    return {str(key): str(item) for key, item in value.items()}


def _string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        # Leave the value untouched so the Pydantic boundary returns its
        # standard ValidationError instead of silently coercing a command line
        # into tokens.
        return value  # type: ignore[return-value]
    return [str(item) for item in value]


def _normalize_transport(raw: Mapping[str, Any]) -> Dict[str, Any]:
    transport = dict(raw)
    transport_type = str(transport.get("type") or "stdio")
    if transport_type not in SUPPORTED_TYPES:
        raise ValueError(f"Unsupported MCP transport type: {transport_type}")

    transport["type"] = transport_type
    if transport_type == "stdio":
        command = transport.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("stdio MCP server requires command")
        transport["command"] = command
        transport["args"] = _string_list(transport.get("args"))
        transport["env"] = _string_map(transport.get("env"), "env")
        if transport.get("cwd") is not None:
            transport["cwd"] = str(transport["cwd"])
        transport.pop("url", None)
        transport.pop("headers", None)
    else:
        url = transport.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"{transport_type} MCP server requires url")
        transport["url"] = url
        transport["headers"] = _string_map(transport.get("headers"), "headers")
        transport.pop("command", None)
        transport.pop("args", None)
        transport.pop("env", None)
        transport.pop("cwd", None)
    return transport


def normalize_mcp_server_entry(raw: Mapping[str, Any] | MCPServerConfig) -> MCPServerConfig:
    """Validate and normalize one canonical or common imported MCP entry."""
    if isinstance(raw, MCPServerConfig):
        entry = raw
    else:
        values = dict(raw)
        if "server" not in values:
            # Common .mcp.json and client files map a server id directly to its
            # transport object.  The model handles legacy top-level fields;
            # this explicit copy keeps the validation boundary predictable.
            entry_keys = {
                "id",
                "name",
                "apps",
                "description",
                "tags",
                "homepage",
                "docs",
                "metadata",
            }
            transport = {
                key: values.pop(key)
                for key in list(values)
                if key not in entry_keys
            }
            values["server"] = transport
        values["server"] = _normalize_transport(values["server"])
        entry = MCPServerConfig.model_validate(values)

    _normalize_transport(entry.server.model_dump())
    if not entry.id.strip():
        raise ValueError("MCP server id must not be empty")
    if not entry.name.strip():
        entry.name = entry.id
    return entry


def _canonical_map_entries(value: Mapping[str, Any]) -> Iterable[MCPServerConfig]:
    for key, raw in value.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"MCP server '{key}' must be an object")
        if "server" in raw:
            data = dict(raw)
            data.setdefault("id", str(key))
        else:
            data = {"id": str(key), "name": str(key), **dict(raw)}
        yield normalize_mcp_server_entry(data)


def parse_mcp_json(text: str) -> List[MCPServerConfig]:
    """Parse canonical maps, ``mcpServers`` maps, or one server entry."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid MCP JSON: {exc.msg}") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("MCP JSON must be an object")

    if "mcpServers" in parsed:
        servers = parsed["mcpServers"]
        if not isinstance(servers, Mapping):
            raise ValueError("mcpServers must be an object")
        return list(_canonical_map_entries(servers))

    if {"id", "server"}.issubset(parsed):
        return [normalize_mcp_server_entry(parsed)]

    # A desktop-manager export is an id -> canonical entry map.  A direct
    # client export is an id -> transport map; both are accepted for migration.
    return list(_canonical_map_entries(parsed))


def parse_codex_toml(text: str) -> MCPServerConfig:
    """Parse one Codex ``[mcp_servers.<id>]`` entry.

    ``[mcp.servers.<id>]`` is accepted only as a migration convenience, just
    as desktop managers do when importing older malformed files.
    """
    try:
        root = tomli.loads(text)
    except tomli.TOMLDecodeError as exc:
        raise ValueError(f"Invalid Codex TOML: {exc}") from exc

    servers = root.get("mcp_servers")
    if not isinstance(servers, Mapping) or not servers:
        nested = root.get("mcp")
        servers = nested.get("servers") if isinstance(nested, Mapping) else None
    if not isinstance(servers, Mapping) or not servers:
        # A single direct server configuration is useful in the advanced
        # editor and mirrors the desktop managers' form parser.
        servers = {"server": root} if any(key in root for key in ("type", "command", "url", "args")) else None
    if not isinstance(servers, Mapping) or not servers:
        raise ValueError("Codex TOML must contain [mcp_servers.<id>]")

    server_id, raw = next(iter(servers.items()))
    if not isinstance(raw, Mapping):
        raise ValueError(f"MCP server '{server_id}' must be a table")
    values = dict(raw)
    if "http_headers" in values and "headers" not in values:
        values["headers"] = values.pop("http_headers")
    values["id"] = str(server_id)
    values.setdefault("name", str(server_id))
    return normalize_mcp_server_entry(values)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, Mapping):
        pairs = [f"{key} = {_toml_value(item)}" for key, item in value.items()]
        return "{ " + ", ".join(pairs) + " }"
    raise ValueError(f"Unsupported TOML value: {type(value).__name__}")


def mcp_server_to_codex_toml(entry: MCPServerConfig) -> str:
    """Serialize one canonical entry using Codex's official TOML layout."""
    entry = normalize_mcp_server_entry(entry)
    transport = _transport_dict(entry)
    if transport.get("type") == "stdio":
        transport.pop("headers", None)
    else:
        transport.pop("env", None)
        transport.pop("cwd", None)
        if "headers" in transport:
            transport["http_headers"] = transport.pop("headers")

    lines = [f"[mcp_servers.{entry.id}]"]
    for key, value in transport.items():
        lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines)


def _transport_dict(entry: MCPServerConfig) -> Dict[str, Any]:
    """Return only fields meaningful for the selected transport."""
    raw = entry.server.model_dump(exclude_none=True, by_alias=True)
    transport_type = raw.get("type", "stdio")
    if transport_type == "stdio":
        keys = {"type", "command", "args", "env", "cwd"}
    else:
        keys = {"type", "url", "headers"}
    known = {
        key: value
        for key, value in raw.items()
        if key in keys and value not in ({}, [])
    }
    # Interoperable managers explicitly keep extension fields (for example
    # timeout_ms) so they survive an import/edit/export cycle.  They are
    # carried after the known transport fields and are not used for
    # secret-value search.
    known.update(
        {
            key: value
            for key, value in raw.items()
            if key not in {"type", "command", "args", "env", "cwd", "url", "headers"}
        }
    )
    known["type"] = transport_type
    return known


def project_mcp_server(entry: MCPServerConfig, client: str) -> Dict[str, Any]:
    """Project the canonical transport into a client-specific live shape."""
    entry = normalize_mcp_server_entry(entry)
    transport = _transport_dict(entry)
    # This is a Kirara connection-lifecycle budget.  It must not be copied
    # into downstream client configs where the field has different semantics.
    transport.pop("startup_timeout_ms", None)
    if not entry.server.roots:
        transport.pop("roots", None)

    if client in {"claude", "claude-desktop", "gemini", "hermes"}:
        return transport
    if client == "codex":
        if "headers" in transport:
            transport["http_headers"] = transport.pop("headers")
        return transport
    if client == "opencode":
        if transport["type"] == "stdio":
            projected: Dict[str, Any] = {
                "type": "local",
                "command": [transport.pop("command"), *transport.pop("args", [])],
            }
            if transport.get("env"):
                projected["environment"] = transport.pop("env")
            transport.pop("type", None)
            transport.pop("cwd", None)
        else:
            projected = {"type": "remote"}
            projected.update(transport)
            projected["type"] = "remote"
        projected["enabled"] = True
        return projected
    raise ValueError(f"Unsupported MCP client projection: {client}")
