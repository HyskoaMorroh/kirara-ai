"""Local, bounded and secret-safe first-run diagnostics."""

import asyncio
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional

from ruamel.yaml import YAML

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.im.adapter import AdapterHealthProvider
from kirara_ai.im.manager import IMManager
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.workflow.core.block.registry import BlockRegistry
from kirara_ai.workflow.core.dispatch import DispatchRuleRegistry
from kirara_ai.workflow.core.dispatch.models.dispatch_rules import CombinedDispatchRule
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from kirara_ai.workflow.core.workflow.validation import validate_workflow_definition

from .models import ReadinessCheck, ReadinessResponse, ReadinessStatus

CHECK_IDS = (
    "data_directories_writable",
    "configuration_parseable",
    "workflows_valid",
    "dispatch_targets_exist",
    "im_available",
    "llm_available",
    "mcp_health",
)

READINESS_WORKERS = 4
_READINESS_EXECUTOR = ThreadPoolExecutor(
    max_workers=READINESS_WORKERS, thread_name_prefix="readiness"
)
_READINESS_SLOTS = threading.BoundedSemaphore(READINESS_WORKERS)


def _check(
    check_id: str,
    status: ReadinessStatus,
    summary: str,
    remediation: str,
    **evidence,
) -> ReadinessCheck:
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


def _configuration_parseable(
    config: GlobalConfig, config_path: Optional[Path]
) -> ReadinessCheck:
    # Parse user-owned source when present. Never serialize values into evidence.
    if config_path is not None and config_path.is_file():
        yaml = YAML(typ="safe")
        with config_path.open("r", encoding="utf-8") as config_file:
            raw_config = yaml.load(config_file)
        GlobalConfig.model_validate(raw_config)
        source = "disk"
    else:
        GlobalConfig.model_validate(config)
        source = "loaded"
    return _check(
        CHECK_IDS[1], "pass", "配置结构可解析", "无需处理",
        section_count=len(GlobalConfig.model_fields), source=source,
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
    disk_files = 0
    workflow_root = Path(registry.workflows_dir)
    if workflow_root.is_dir():
        for path in workflow_root.glob("*/*.yaml"):
            disk_files += 1
            try:
                WorkflowBuilder.load_from_yaml(str(path), registry.container)
            except Exception:
                invalid += 1
    status: ReadinessStatus = "pass" if invalid == 0 else "fail"
    return _check(
        CHECK_IDS[2], status,
        "工作流结构有效" if invalid == 0 else "存在无效工作流",
        "无需处理" if invalid == 0 else "在工作流编辑器中修复无效节点或连线",
        workflow_count=len(builders), invalid_workflow_count=invalid,
        issue_count=issue_count, disk_file_count=disk_files,
    )


def _dispatch_targets(
    workflow_registry: WorkflowRegistry, dispatch_registry: DispatchRuleRegistry
) -> ReadinessCheck:
    rules = dispatch_registry.get_all_rules()
    missing = sum(
        1 for rule in rules
        if rule.enabled and workflow_registry.get(rule.workflow_id) is None
    )
    invalid_files = 0
    disk_rule_count = 0
    rules_root = Path(dispatch_registry.rules_dir)
    if rules_root.is_dir():
        yaml = YAML(typ="safe")
        for path in rules_root.glob("*.yaml"):
            try:
                with path.open("r", encoding="utf-8") as rules_file:
                    raw_rules = yaml.load(rules_file)
                if not isinstance(raw_rules, list):
                    raise ValueError("rules document must be a list")
                for raw_rule in raw_rules:
                    disk_rule_count += 1
                    if not isinstance(raw_rule, dict):
                        raise ValueError("rule must be a mapping")
                    if "rule_groups" in raw_rule:
                        disk_rule = CombinedDispatchRule.model_validate(raw_rule)
                    else:
                        disk_rule = dispatch_registry._convert_old_rule(raw_rule)
                    if (
                        disk_rule.enabled
                        and workflow_registry.get(disk_rule.workflow_id) is None
                    ):
                        missing += 1
            except Exception:
                invalid_files += 1
    failed = missing > 0 or invalid_files > 0
    return _check(
        CHECK_IDS[3], "fail" if failed else "pass",
        "调度目标和文件有效" if not failed else "调度规则文件或目标无效",
        "无需处理" if not failed else "修复无效规则文件或失效工作流引用",
        rule_count=len(rules), missing_target_count=missing,
        disk_rule_count=disk_rule_count, invalid_file_count=invalid_files,
    )


def _im_availability(config: GlobalConfig, manager: IMManager) -> ReadinessCheck:
    enabled = [item.name for item in config.ims if item.enable]
    counts = {
        "connected": 0,
        "waiting": 0,
        "disconnected": 0,
        "stale": 0,
        "initializing": 0,
        "credential_rejected": 0,
        "upstream_refused": 0,
        "storage_unavailable": 0,
    }
    # 上游自身的扫码登录状态。与 counts 分开统计：`waiting` 说的是「Kirara 还没
    # 被连上」，`qr_waiting_scan` 说的是「上游连上了、但 QQ 还没登录」。
    # 前者要查地址与 Token，后者要去扫码——混成一个数字会把处置指向错误方向。
    qr_states: dict[str, int] = {}
    for name in enabled:
        if not manager.is_adapter_running(name):
            counts["disconnected"] += 1
            continue
        adapter = manager.adapters.get(name)
        if isinstance(adapter, AdapterHealthProvider):
            snapshot = adapter.get_health_snapshot()
            # An adapter is free to add a status this readiness check does not
            # know yet; count it as not-connected rather than raising KeyError
            # and taking the whole readiness endpoint down.
            if snapshot.status in counts:
                counts[snapshot.status] += 1
            else:
                counts["disconnected"] += 1
            qr_login = getattr(snapshot, "qr_login", None)
            if qr_login is not None:
                key = f"qr_{qr_login.state}"
                qr_states[key] = qr_states.get(key, 0) + 1
        else:
            counts["connected"] += 1

    available = counts["connected"]
    status: ReadinessStatus = "pass" if not enabled or available == len(enabled) else "warn"
    # A rejected credential or refused handshake is not a "wait and it will
    # settle" state, so say what to fix instead of pointing at the heartbeat.
    blocked = counts["credential_rejected"] + counts["upstream_refused"]
    # 上游连上了但 QQ 尚未登录，处置是「去扫码」而不是「查连接」。
    # 不给出这条区分，操作者会在一个其实只差扫码的实例上反复检查地址与 Token。
    awaiting_scan = qr_states.get("qr_waiting_scan", 0) + qr_states.get(
        "qr_expired", 0
    ) + qr_states.get("qr_scanned", 0)
    # 存储故障排在最前：链路可能完全正常，但每一条要落库的投递都在失败。
    # 处置是查挂载与磁盘，与「查 Token」「去扫码」完全是两个方向。
    storage_broken = counts["storage_unavailable"]
    if status == "pass":
        summary = "IM 适配器已连接"
        remediation = "无需处理"
    elif storage_broken:
        summary = "部分 IM 适配器的持久化目录不可写"
        remediation = "检查数据卷是否被只读重挂或磁盘写满，恢复写权限后无需重启适配器"
    elif blocked:
        summary = "部分 IM 适配器的上游连接被拒绝"
        remediation = "核对适配器访问令牌与上游反向连接配置，再查看连接原因码"
    elif awaiting_scan:
        summary = "部分 IM 适配器的上游仍在等待扫码登录"
        remediation = "取最新二维码路径完成扫码登录；不要扫已过期的旧二维码"
    else:
        summary = "部分 IM 适配器尚未建立连接"
        remediation = "检查 IM 适配器运行状态、登录状态和连接心跳"
    return _check(
        CHECK_IDS[4], status,
        summary,
        remediation,
        configured_count=len(enabled),
        available_count=available,
        connected_count=counts["connected"],
        waiting_count=counts["waiting"],
        disconnected_count=counts["disconnected"],
        stale_count=counts["stale"],
        initializing_count=counts["initializing"],
        credential_rejected_count=counts["credential_rejected"],
        upstream_refused_count=counts["upstream_refused"],
        storage_unavailable_count=counts["storage_unavailable"],
        **qr_states,
    )


def _llm_availability(config: GlobalConfig, manager: LLMManager) -> ReadinessCheck:
    enabled = [item.name for item in config.llms.api_backends if item.enable]
    available = sum(bool(manager.is_backend_available(name)) for name in enabled)
    status: ReadinessStatus = (
        "pass" if not enabled or available == len(enabled) else "warn"
    )
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
    status: ReadinessStatus = "pass" if connected == len(configured) else "warn"
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
    config_path: Optional[Path] = None,
    block_registry: Optional[BlockRegistry] = None,
    timeout_seconds: float = 1.0,
) -> ReadinessResponse:
    """Run each local check independently with a strict per-check timeout."""

    checks: tuple[tuple[str, Callable[[], ReadinessCheck]], ...] = (
        (CHECK_IDS[0], lambda: _writable_directories(Path(data_path), workflow_registry.workflows_dir)),
        (CHECK_IDS[1], lambda: _configuration_parseable(config, config_path)),
        (CHECK_IDS[2], lambda: _workflow_validity(workflow_registry, block_registry)),
        (CHECK_IDS[3], lambda: _dispatch_targets(workflow_registry, dispatch_registry)),
        (CHECK_IDS[4], lambda: _im_availability(config, im_manager)),
        (CHECK_IDS[5], lambda: _llm_availability(config, llm_manager)),
        (CHECK_IDS[6], lambda: _mcp_health(config, mcp_manager)),
    )
    results = []
    for check_id, operation in checks:
        if not _READINESS_SLOTS.acquire(blocking=False):
            results.append(
                _check(
                    check_id, "fail", "诊断容量已满", "稍后重试诊断请求",
                    capacity_exhausted=True,
                )
            )
            continue

        def run_and_release(operation=operation):
            try:
                return operation()
            finally:
                _READINESS_SLOTS.release()

        try:
            future = asyncio.get_running_loop().run_in_executor(
                _READINESS_EXECUTOR, run_and_release
            )
            future.add_done_callback(
                lambda completed: completed.exception()
                if not completed.cancelled()
                else None
            )
            result = await asyncio.wait_for(
                asyncio.shield(future), timeout=timeout_seconds
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
