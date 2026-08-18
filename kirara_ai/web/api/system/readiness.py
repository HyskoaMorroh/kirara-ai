"""Local, bounded and secret-safe first-run diagnostics."""

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.im.manager import IMManager
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.workflow.core.block.registry import BlockRegistry
from kirara_ai.workflow.core.dispatch import DispatchRuleRegistry
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from kirara_ai.workflow.core.workflow.validation import validate_workflow_definition

from .models import ReadinessCheck, ReadinessResponse

CHECK_IDS = (
    "data_directories_writable",
    "configuration_parseable",
    "workflows_valid",
    "dispatch_targets_exist",
    "im_available",
    "llm_available",
    "mcp_health",
)


def _check(check_id: str, status: str, summary: str, remediation: str, **evidence):
    return ReadinessCheck(
        id=check_id,
        status=status,
        summary=summary,
        remediation=remediation,
        evidence=evidence,
    )


def _writable_directories(data_path: Path, workflows_dir: str) -> ReadinessCheck:
    roots = [data_path, Path(workflows_dir)]
    tested = 0
    for root in roots:
        probe_root = root if root.exists() else root.parent
        if not probe_root.exists() or not probe_root.is_dir():
            return _check(
                CHECK_IDS[0], "fail", "数据目录不可用", "创建数据目录并授予当前用户写权限",
                directories_tested=tested,
            )
        try:
            descriptor, probe = tempfile.mkstemp(prefix=".readiness-", dir=probe_root)
            os.close(descriptor)
            Path(probe).unlink(missing_ok=True)
            tested += 1
        except OSError:
            return _check(
                CHECK_IDS[0], "fail", "数据目录不可写", "授予当前用户数据目录写权限并检查磁盘空间",
                directories_tested=tested,
            )
    return _check(
        CHECK_IDS[0], "pass", "数据目录可写", "无需处理",
        directories_tested=tested,
    )


def _configuration_parseable(config: GlobalConfig) -> ReadinessCheck:
    # Validate the already-loaded model without serializing configuration values.
    GlobalConfig.model_validate(config)
    return _check(
        CHECK_IDS[1], "pass", "配置结构可解析", "无需处理",
        section_count=len(GlobalConfig.model_fields),
    )


def _workflow_validity(
    registry: WorkflowRegistry, block_registry: Optional[BlockRegistry]
) -> ReadinessCheck:
    builders = registry.snapshot_builders()
    invalid = 0
    issue_count = 0
    if block_registry is not None:
        for _, builder in builders:
            blocks = [
                SimpleNamespace(
                    name=node.name,
                    type_name=block_registry.get_block_type_name(node.spec.block_class),
                    config=node.spec.kwargs,
                )
                for node in builder.nodes
            ]
            wires = [
                SimpleNamespace(
                    source_block=source,
                    source_output=source_output,
                    target_block=target,
                    target_input=target_input,
                )
                for source, source_output, target, target_input in builder.wire_specs
            ]
            errors = [
                issue
                for issue in validate_workflow_definition(blocks, wires, block_registry)
                if issue.severity == "error"
            ]
            if errors:
                invalid += 1
                issue_count += len(errors)
    status = "pass" if invalid == 0 else "fail"
    return _check(
        CHECK_IDS[2], status,
        "工作流结构有效" if invalid == 0 else "存在无效工作流",
        "无需处理" if invalid == 0 else "在工作流编辑器中修复无效节点或连线",
        workflow_count=len(builders), invalid_workflow_count=invalid, issue_count=issue_count,
    )


def _dispatch_targets(
    workflow_registry: WorkflowRegistry, dispatch_registry: DispatchRuleRegistry
) -> ReadinessCheck:
    rules = dispatch_registry.get_all_rules()
    missing = sum(
        1 for rule in rules
        if rule.enabled and workflow_registry.get(rule.workflow_id) is None
    )
    return _check(
        CHECK_IDS[3], "pass" if missing == 0 else "fail",
        "调度目标存在" if missing == 0 else "调度规则引用了不存在的工作流",
        "无需处理" if missing == 0 else "修复或禁用引用失效工作流的调度规则",
        rule_count=len(rules), missing_target_count=missing,
    )


def _im_availability(config: GlobalConfig, manager: IMManager) -> ReadinessCheck:
    enabled = [item.name for item in config.ims if item.enable]
    available = sum(bool(manager.is_adapter_running(name)) for name in enabled)
    status = "pass" if not enabled or available == len(enabled) else "warn"
    return _check(
        CHECK_IDS[4], status,
        "IM 适配器可用" if status == "pass" else "部分已配置 IM 适配器未运行",
        "无需处理" if status == "pass" else "检查 IM 适配器配置和连接状态",
        configured_count=len(enabled), available_count=available,
    )


def _llm_availability(config: GlobalConfig, manager: LLMManager) -> ReadinessCheck:
    enabled = [item.name for item in config.llms.api_backends if item.enable]
    available = sum(bool(manager.is_backend_available(name)) for name in enabled)
    status = "pass" if not enabled or available == len(enabled) else "warn"
    return _check(
        CHECK_IDS[5], status,
        "LLM 后端可用" if status == "pass" else "部分已配置 LLM 后端不可用",
        "无需处理" if status == "pass" else "检查模型后端配置和已加载模型",
        configured_count=len(enabled), available_count=available,
    )


def _mcp_health(config: GlobalConfig, manager: MCPServerManager) -> ReadinessCheck:
    configured = [server for server in config.mcp.servers if server.enable]
    if not configured:
        return _check(
            CHECK_IDS[6], "skip", "未配置可选 MCP 服务", "无需处理",
            configured_count=0, connected_count=0,
        )
    statistics = manager.get_statistics()
    connected = int(statistics.get("connected", 0))
    status = "pass" if connected == len(configured) else "warn"
    return _check(
        CHECK_IDS[6], status,
        "MCP 服务已连接" if status == "pass" else "部分可选 MCP 服务未连接",
        "无需处理" if status == "pass" else "检查 MCP 服务进程或连接配置",
        configured_count=len(configured), connected_count=connected,
    )


async def run_readiness_checks(
    config: GlobalConfig,
    workflow_registry: WorkflowRegistry,
    dispatch_registry: DispatchRuleRegistry,
    im_manager: IMManager,
    llm_manager: LLMManager,
    mcp_manager: MCPServerManager,
    *,
    data_path: Path,
    block_registry: Optional[BlockRegistry] = None,
    timeout_seconds: float = 1.0,
) -> ReadinessResponse:
    """Run each local check independently with a strict per-check timeout."""

    checks: tuple[tuple[str, Callable[[], ReadinessCheck]], ...] = (
        (CHECK_IDS[0], lambda: _writable_directories(Path(data_path), workflow_registry.workflows_dir)),
        (CHECK_IDS[1], lambda: _configuration_parseable(config)),
        (CHECK_IDS[2], lambda: _workflow_validity(workflow_registry, block_registry)),
        (CHECK_IDS[3], lambda: _dispatch_targets(workflow_registry, dispatch_registry)),
        (CHECK_IDS[4], lambda: _im_availability(config, im_manager)),
        (CHECK_IDS[5], lambda: _llm_availability(config, llm_manager)),
        (CHECK_IDS[6], lambda: _mcp_health(config, mcp_manager)),
    )
    results = []
    for check_id, operation in checks:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(operation), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            result = _check(
                check_id, "fail", "诊断超时", "检查本地磁盘或服务管理器状态",
                timed_out=True,
            )
        except Exception as error:
            # Exception text may contain configuration values, so expose only its type.
            result = _check(
                check_id, "fail", "诊断无法完成", "检查本地配置和服务初始化状态",
                error_type=type(error).__name__,
            )
        results.append(result)
    return ReadinessResponse(
        ready=not any(check.status == "fail" for check in results),
        timestamp=datetime.now(timezone.utc),
        checks=results,
    )
