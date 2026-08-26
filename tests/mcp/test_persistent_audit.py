from __future__ import annotations

import time

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.mcp_module.audit_store import MCPAuditStore
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService


def _container() -> DependencyContainer:
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    return container


def test_mcp_audit_survives_manager_rebuild_and_supports_filters(tmp_path):
    database = tmp_path / "mcp" / "audit.db"
    first = MCPServerManager(_container(), audit_store=MCPAuditStore(database))
    first._audit_operation("docs", "connect", time.monotonic(), "success")
    first._audit_operation("docs", "call_tool", time.monotonic(), "denied")
    first._audit_operation("other", "connect", time.monotonic(), "success")

    second = MCPServerManager(_container(), audit_store=MCPAuditStore(database))
    page = second.list_audit(server_id="docs", operation="call_tool")

    assert page["persistent"] is True
    assert page["total"] == 1
    assert page["items"][0]["server"] == "docs"
    assert page["items"][0]["outcome"] == "denied"


def test_mcp_audit_store_applies_retention_limit_without_raw_payloads(tmp_path):
    store = MCPAuditStore(tmp_path / "audit.db", retention_limit=2)
    for index in range(3):
        store.append(
            {
                "component": "mcp",
                "timestamp": f"2026-01-01T00:00:0{index}Z",
                "server": "docs",
                "operation": "call_tool",
                "duration_ms": index,
                "outcome": "error",
                "error": {"type": "RuntimeError", "message": "operation failed"},
                "tool_args": {"secret": "private"},
            }
        )

    page = store.list(limit=10)

    assert page["total"] == 2
    assert [row["duration_ms"] for row in page["items"]] == [2, 1]
    assert "tool_args" not in page["items"][0]
    assert "private" not in str(page)


def test_mcp_audit_falls_back_to_memory_when_persistent_write_fails():
    class FailingAuditStore:
        def append(self, record):
            raise OSError("storage unavailable")

        def list(self, **kwargs):
            raise AssertionError("failed stores must be disabled")

    manager = MCPServerManager(_container(), audit_store=FailingAuditStore())
    manager._audit_operation("docs", "connect", time.monotonic(), "success")

    page = manager.list_audit(server_id="docs")

    assert page["persistent"] is False
    assert page["total"] == 1
    assert page["items"][0]["operation"] == "connect"


def test_mcp_audit_falls_back_to_memory_when_persistent_query_fails():
    class FailingAuditStore:
        def append(self, record):
            return None

        def list(self, **kwargs):
            raise OSError("storage unavailable")

    manager = MCPServerManager(_container(), audit_store=FailingAuditStore())
    manager._audit_operation("docs", "connect", time.monotonic(), "success")

    page = manager.list_audit(server_id="docs")

    assert page["persistent"] is False
    assert page["total"] == 1
    assert page["items"][0]["operation"] == "connect"


def test_mcp_audit_is_forwarded_to_unified_resource_audit(tmp_path):
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    manager = MCPServerManager(
        _container(),
        audit_sink=lifecycle.append_runtime_audit,
        audit_store=MCPAuditStore(tmp_path / "mcp" / "audit.db"),
    )

    manager._audit_operation(
        "context7",
        "call_tool",
        time.monotonic(),
        "success",
        correlation_id="turn-correlation-123",
    )

    page = lifecycle.list_audit(
        component="mcp",
        server="context7",
        correlation_id="turn-correlation-123",
    )
    assert page["total"] == 1
    assert page["items"][0]["resource_id"] == "mcp.context7"
    assert page["items"][0]["operation"] == "call_tool"
    assert page["items"][0]["outcome"] == "success"


def test_mcp_audit_sink_failure_does_not_interrupt_operation():
    def reject_audit(_record):
        raise OSError("unavailable")

    manager = MCPServerManager(_container(), audit_sink=reject_audit)

    manager._audit_operation("docs", "connect", time.monotonic(), "success")

    assert manager.audit_records[-1]["operation"] == "connect"
