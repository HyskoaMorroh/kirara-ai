import asyncio
from collections.abc import Mapping
import os
import secrets
import signal
import time
from pathlib import Path
from typing import Any

from kirara_ai.config import DATA_PATH
from kirara_ai.config.config_loader import ConfigLoader
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.database import DatabaseManager
from kirara_ai.events.application import ApplicationStarted, ApplicationStopping
from kirara_ai.events.event_bus import EventBus
from kirara_ai.im.delivery_timing_store import DeliveryTimingStore
from kirara_ai.im.im_registry import IMRegistry
from kirara_ai.im.manager import IMManager
from kirara_ai.internal import shutdown_event
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.llm_registry import LLMBackendRegistry
from kirara_ai.llm.pricing import PriceCatalog
from kirara_ai.logger import get_logger
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.media import MediaManager
from kirara_ai.media.carrier import MediaCarrierRegistry, MediaCarrierService
from kirara_ai.memory.composes import DefaultMemoryComposer, DefaultMemoryDecomposer, MultiElementDecomposer
from kirara_ai.memory.memory_manager import MemoryManager
from kirara_ai.memory.scopes import GlobalScope, GroupScope, MemberScope
from kirara_ai.plugin_manager.plugin_loader import PluginLoader
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_sources import ResourceSourceService
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.system_dependencies import SystemDependencyService
from kirara_ai.plugin_manager.extension_host import ExtensionLifecycleHost
from kirara_ai.plugin_manager.models import LifecycleName
from kirara_ai.scheduler import TaskScheduler
from kirara_ai.tracing import LLMTracer, TracingManager
from kirara_ai.web.api.system.utils import (
    get_installed_version,
    get_latest_pypi_version,
    is_newer_release,
)
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.block import BlockRegistry
from kirara_ai.workflow.core.dispatch import DispatchRuleRegistry, WorkflowDispatcher
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from kirara_ai.workflow.implementations.blocks import register_system_blocks
from kirara_ai.workflow.implementations.rules import register_system_dispatch_rules, validate_rule_workflows
from kirara_ai.workflow.implementations.workflows import register_system_workflows

logger = get_logger("Entrypoint")

# config.yaml.example 中的示例密钥，视为未设置
PLACEHOLDER_SECRET_KEY = "please-change-this-to-a-secure-secret-key"

_interrupt_count = 0  # 添加计数器

async def check_update(config: GlobalConfig):
    """检查更新。

    `update.disable_auto_check` 打开时**完全不发起请求**：离线或内网部署既查不到
    注册表又要等超时，而「禁用」如果只是不打印结果，那条超时等待依然存在。
    WebUI 的「检查更新」按钮仍然可用——那是用户主动发起的，不受这里影响。
    """
    if config.update.disable_auto_check:
        logger.info("Automatic update check is disabled by configuration.")
        return
    running_version = get_installed_version()
    logger.info("Checking for updates...")
    latest_version, _ = await get_latest_pypi_version(
        "kirara-ai", config.update.pypi_registry
    )
    logger.info(f"Running version: {running_version}, Latest version: {latest_version}")
    backend_update_available = is_newer_release(latest_version, running_version)
    if backend_update_available:
        logger.warning(f"New version {latest_version} is available. Please update to the latest version.")
        logger.warning(f"You can download the latest version from WebUI")

# 注册信号处理函数
def _signal_handler(*args):
    global _interrupt_count
    _interrupt_count += 1

    if _interrupt_count == 1:
        if not shutdown_event.is_set():
            logger.warning("Interrupt signal received. Stopping application...")
            shutdown_event.set()
    elif _interrupt_count == 2:
        logger.warning("Interrupt signal received again. Press Ctrl+C one more time to force shutdown...")
    else:
        logger.warning("Interrupt signal received for the third time. Forcing shutdown...")
        os._exit(1)


def init_container() -> DependencyContainer:
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    return container


def notify_extension_lifecycle(
    container: DependencyContainer, lifecycle: LifecycleName
) -> None:
    """Produce a sanitized application lifecycle notification when configured."""
    if not container.has(ExtensionLifecycleHost):
        return
    status = "started" if lifecycle == "startup_completed" else "stopping"
    container.resolve(ExtensionLifecycleHost).emit(
        lifecycle, {"component": "application", "status": status}
    )


def init_memory_system(container: DependencyContainer):
    """初始化记忆系统"""
    memory_manager = MemoryManager(container)

    # 注册默认作用域
    memory_manager.register_scope("member", MemberScope)
    memory_manager.register_scope("group", GroupScope)
    memory_manager.register_scope("global", GlobalScope)

    # 注册默认组合器和解析器
    memory_manager.register_composer("default", DefaultMemoryComposer)
    memory_manager.register_decomposer("default", DefaultMemoryDecomposer)
    memory_manager.register_decomposer("multi_element", MultiElementDecomposer)

    container.register(MemoryManager, memory_manager)
    return memory_manager

def init_media_carrier(container: DependencyContainer):
    """初始化媒体载体"""
    # 注册记忆管理器作为媒体引用提供者
    carrier_registry = container.resolve(MediaCarrierRegistry)
    carrier_registry.register("memory", container.resolve(MemoryManager))

def init_tracing_system(container: DependencyContainer):
    """初始化追踪系统"""
    logger.info("Initializing tracing system...")

    # 初始化追踪管理器
    tracing_manager = TracingManager(container)
    container.register(TracingManager, tracing_manager)

    # 创建并注册LLM追踪器
    llm_tracer = LLMTracer(container)
    container.register(LLMTracer, llm_tracer)
    tracing_manager.register_tracer("llm", llm_tracer)

    # 初始化追踪系统
    tracing_manager.initialize()

    logger.info("Tracing system initialized")
    return tracing_manager


def _config_path() -> Path:
    """Return the configuration file in the mounted application data volume."""
    return Path(DATA_PATH) / "config.yaml"


def _agent_debug_hook(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return bounded state metadata without exposing Hook input or host data."""

    allowed_fields = {"event", "tool_name", "is_error", "message_count"}
    return {
        "status": "ok",
        "fields": sorted(str(key) for key in payload if str(key) in allowed_fields),
        "field_count": min(len(payload), len(allowed_fields)),
    }


def _agent_audit_hook(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return bounded metadata for the built-in Agent audit declarations."""

    allowed_fields = {
        "agent_id",
        "confirmed",
        "estimated_chars",
        "has_images",
        "is_error",
        "iteration",
        "message_count",
        "model_id",
        "session_key",
        "tool_name",
    }
    return {
        "status": "recorded",
        "fields": sorted(str(key) for key in payload if str(key) in allowed_fields),
        "field_count": min(len(payload), len(allowed_fields)),
    }


def _agent_runtime_audit_sink(
    record: Mapping[str, Any],
    *,
    resource_service: ResourceLifecycleService | None = None,
) -> None:
    """Write only a small, recursively redacted Agent runtime audit summary."""

    sensitive_parts = (
        "password",
        "token",
        "secret",
        "cookie",
        "authorization",
        "credential",
        "api_key",
        "private_key",
    )
    private_value_keys = {
        "prompt",
        "payload",
        "input",
        "output",
        "result",
        "content",
        "message",
    }

    def redact(value: Any, key: str = "") -> Any:
        if key.lower() in private_value_keys or any(
            part in key.lower() for part in sensitive_parts
        ):
            return "[redacted]"
        if isinstance(value, Mapping):
            return {str(item): redact(child, str(item)) for item, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [redact(item, key) for item in value[:32]]
        if isinstance(value, str):
            return value[:256]
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        return type(value).__name__

    summary = redact(record)
    logger.info("Agent runtime audit: {}", summary)
    if resource_service is not None:
        try:
            resource_service.append_runtime_audit(summary)
        except Exception:
            # Audit persistence is deliberately isolated from Agent execution.
            logger.debug("Unable to persist Agent runtime audit")


def init_agent_runtime(container: DependencyContainer):
    """Register the shared Agent runtime against the application's services."""
    # Import lazily because Runtime imports the message package, whose package
    # initialisation also loads plugin-facing IM interfaces.
    from kirara_ai.agent_runtime import (
        AgentHookRuntime,
        AgentRegistry,
        AgentRuntimeExecutor,
        HookHandler,
        SessionStore,
    )

    agent_registry = AgentRegistry(DATA_PATH)
    session_store = SessionStore(DATA_PATH)
    resource_service = container.resolve(ResourceLifecycleService)
    config = container.resolve(GlobalConfig) if container.has(GlobalConfig) else GlobalConfig()
    runtime_config = config.agent_runtime
    hook_handlers = {
        name: HookHandler(_agent_audit_hook)
        for name in (
            "audit.agent_start",
            "audit.user_prompt",
            "audit.pre_tool",
            "audit.permission_request",
            "audit.post_tool",
            "audit.pre_compact",
            "audit.post_compact",
            "audit.stop",
        )
    }
    if runtime_config.debug_hooks_enabled:
        hook_handlers["agent.debug"] = HookHandler(_agent_debug_hook)
    hook_runtime = AgentHookRuntime(
        resource_loader=resource_service.read_entry,
        resource_service=resource_service,
        handlers=hook_handlers,
        audit_sink=lambda record: _agent_runtime_audit_sink(
            record, resource_service=resource_service
        ),
    )
    memory_manager = (
        container.resolve(MemoryManager)
        if container.has(MemoryManager)
        else None
    )
    # 服务器组件就绪状态。传给运行时，是为了让技能广告能说出「这个命令在服务器上
    # 还没装」——没有它时最坏的表现不是报错，而是模型照着一份它执行不了的说明
    # 自信作答（「我已经打开了浏览器」），而用户看不出与真的成功有什么区别。
    #
    # 容器里没有就传 None：嵌入式用法与既有测试的容器都不注册它，
    # 缺它只少一句提示，不该让整个 Agent 运行时装配不起来。
    dependency_service = (
        container.resolve(SystemDependencyService)
        if container.has(SystemDependencyService)
        else None
    )
    runtime = AgentRuntimeExecutor(
        agent_registry=agent_registry,
        llm_manager=container.resolve(LLMManager),
        mcp_manager=container.resolve(MCPServerManager),
        resource_loader=resource_service.read_entry,
        resource_service=resource_service,
        dependency_service=dependency_service,
        session_store=session_store,
        memory_manager=memory_manager,
        hook_runtime=hook_runtime,
        context_char_threshold=runtime_config.context_char_threshold,
        reply_stream_mode=runtime_config.reply_stream_mode,
        channel_reply_stream_modes=runtime_config.channel_reply_stream_modes,
        tool_search_threshold=runtime_config.tool_search_threshold,
        turn_deadline_seconds=runtime_config.turn_deadline_seconds,
        audit_sink=lambda record: _agent_runtime_audit_sink(
            record, resource_service=resource_service
        ),
    )
    container.register(AgentRegistry, agent_registry)
    container.register(SessionStore, session_store)
    container.register(AgentRuntimeExecutor, runtime)
    return runtime


def init_storage(container: DependencyContainer) -> tuple[DatabaseManager, MediaManager]:
    """Initialize persistent services below the configured VPS data root."""

    data_root = Path(DATA_PATH).resolve()
    db = DatabaseManager(container, data_dir=data_root / "db")
    db.initialize()
    container.register(DatabaseManager, db)

    media_manager = MediaManager(media_dir=data_root / "media")
    container.register(MediaManager, media_manager)
    container.register(MediaCarrierRegistry, MediaCarrierRegistry(container))
    container.register(MediaCarrierService, MediaCarrierService(container, media_manager))

    # 回复耗时落库：日志只能回答「刚才那条为什么慢」，按时间范围回查历史需要行。
    # 只记录时长与计数，不记录任何消息正文。
    timing_store = DeliveryTimingStore(db)
    try:
        removed = timing_store.cleanup()
        if removed:
            logger.info(f"已清理 {removed} 条超出保留期的投递耗时记录")
    except Exception as error:  # noqa: BLE001 - 清理失败不应阻止启动
        logger.warning(f"投递耗时记录清理失败：{error}")
    container.register(DeliveryTimingStore, timing_store)
    return db, media_manager


def init_pricing_system(container: DependencyContainer) -> PriceCatalog:
    """Initialize the durable provider pricing catalog below the data root."""

    catalog_path = Path(DATA_PATH).resolve() / "pricing" / "catalog.json"
    catalog = PriceCatalog.load_or_create(catalog_path)
    container.register(PriceCatalog, catalog)
    return catalog


def init_application() -> DependencyContainer:
    """初始化应用程序"""
    logger.info("Initializing application...")

    # 配置文件路径
    config_path = _config_path()

    # 加载配置文件
    logger.info(f"Loading configuration from {config_path}")
    # check data directory
    Path(DATA_PATH).mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        config: GlobalConfig = ConfigLoader.load_config(config_path, GlobalConfig)
        logger.info("Configuration loaded successfully")
    else:
        logger.warning(
            f"Configuration file {config_path} not found, using default configuration"
        )
        logger.warning(
            "Please create a configuration file by copying config.yaml.example to config.yaml and modify it according to your needs"
        )
        config = GlobalConfig()

    # 设置时区
    os.environ["TZ"] = config.system.timezone
    if hasattr(time, "tzset"):
        time.tzset()

    # 首次使用时 secret_key 为空（或仍是示例占位值），PyJWT 会因空密钥直接报错
    # InvalidKeyError: HMAC key must not be empty，导致「设置管理员密码」始终提示登录失败。
    # 这里自动生成一个随机密钥并落盘，保证首次设置密码即可正常签发 token，
    # 同时避免每次重启都让已登录的 token 失效。
    if not config.web.secret_key or config.web.secret_key == PLACEHOLDER_SECRET_KEY:
        config.web.secret_key = secrets.token_hex(32)
        logger.warning("Web secret_key is not set, generated a new random secret_key")
        try:
            ConfigLoader.save_config_with_backup(str(config_path), config)
            logger.info(f"Generated secret_key has been saved to {config_path}")
        except Exception as e:
            logger.error(f"Failed to persist generated secret_key: {e}")

    container = init_container()
    container.register(asyncio.AbstractEventLoop, asyncio.new_event_loop())
    container.register(EventBus, EventBus())

    container.register(GlobalConfig, config)
    container.register(BlockRegistry, BlockRegistry())

    init_storage(container)
    init_pricing_system(container)

    # 注册工作流注册表
    workflow_registry = WorkflowRegistry(container)
    container.register(WorkflowRegistry, workflow_registry)
    resource_service = ResourceLifecycleService(
            DATA_PATH,
            workflow_registry=workflow_registry,
            container=container,
        )
    container.register(ResourceLifecycleService, resource_service)
    source_service = ResourceSourceService(resource_service)
    container.register(ResourceSourceService, source_service)
    dependency_service = SystemDependencyService(DATA_PATH)
    container.register(SystemDependencyService, dependency_service)
    catalog_service = ResourceCatalogService(
        resource_service,
        source_service,
        dependency_service,
    )
    container.register(ResourceCatalogService, catalog_service)
    catalog_service.ensure_builtins()

    # 注册调度规则注册表
    dispatch_registry = DispatchRuleRegistry(container)
    container.register(DispatchRuleRegistry, dispatch_registry)

    container.register(IMRegistry, IMRegistry())
    container.register(LLMBackendRegistry, LLMBackendRegistry())

    im_manager = IMManager(container)
    container.register(IMManager, im_manager)

    llm_manager = LLMManager(container)
    container.register(LLMManager, llm_manager)
    plugin_loader = PluginLoader(container, os.path.join(os.path.dirname(__file__), "plugins"))
    container.register(PluginLoader, plugin_loader)

    workflow_dispatcher = WorkflowDispatcher(container)
    container.register(WorkflowDispatcher, workflow_dispatcher)

    container.register(WebServer, WebServer(container))

    mcp_manager = MCPServerManager(
        container,
        audit_sink=resource_service.append_runtime_audit,
    )
    container.register(MCPServerManager, mcp_manager)

    # 初始化记忆系统
    logger.info("Initializing memory system...")
    init_memory_system(container)

    init_media_carrier(container)

    # 初始化追踪系统
    init_tracing_system(container)

    # 注册系统 blocks
    register_system_blocks(container.resolve(BlockRegistry))

    # 发现并加载插件
    plugin_loader = container.resolve(PluginLoader)
    logger.info("Discovering internal plugins...")
    plugin_loader.discover_internal_plugins()
    logger.info("Discovering external plugins...")
    plugin_loader.discover_external_plugins()
    logger.info("Loading plugins")
    plugin_loader.load_plugins()

    # 加载工作流和调度规则
    workflow_registry = container.resolve(WorkflowRegistry)
    workflow_registry.load_workflows()
    register_system_workflows(workflow_registry)
    dispatch_registry = container.resolve(DispatchRuleRegistry)
    dispatch_registry.load_rules()
    # 内置默认规则放在 load_rules 之后：用户已有的同 id 规则优先保留，
    # 空目录（如全新的 pip 安装）则由内置默认值兜底，保证开箱即可对话
    register_system_dispatch_rules(dispatch_registry)
    # 规则引用的工作流可能已被用户删除（tombstone）。这里在启动阶段就把失效引用
    # 降级为 chat:normal 或禁用，否则每条消息都会抛 WorkflowNotFoundException。
    validate_rule_workflows(dispatch_registry, logger)

    # 加载模型
    llm_manager = container.resolve(LLMManager)
    logger.info("Loading LLMs")
    llm_manager.load_config()

    # 加载MCP服务器
    mcp_manager = container.resolve(MCPServerManager)
    logger.info("Loading MCP servers")
    mcp_manager.load_servers()

    # All inbound channels use this one runtime once an Agent is configured.
    # Legacy workflow dispatch remains available when no Agent is configured.
    init_agent_runtime(container)

    # 注册定时任务调度器（负责按周期自动检测模型列表）
    container.register(TaskScheduler, TaskScheduler(container))

    return container

def run_application(container: DependencyContainer):
    """运行应用程序"""
    loop = container.resolve(asyncio.AbstractEventLoop)

    # 启动Web服务器
    logger.info("Starting web server...")
    web_server = container.resolve(WebServer)
    loop.run_until_complete(web_server.start())

    # 启动插件
    plugin_loader = container.resolve(PluginLoader)
    plugin_loader.start_plugins()

    # 启动适配器
    logger.info("Starting adapters")
    im_manager = container.resolve(IMManager)
    im_manager.start_adapters(loop=loop)

    # 加载MCP服务器
    mcp_manager = container.resolve(MCPServerManager)
    logger.info("Connecting to MCP servers")
    mcp_manager.connect_all_servers(loop=loop)

    # 启动定时任务调度器
    task_scheduler = container.resolve(TaskScheduler)
    task_scheduler.start(loop)

    # 启动媒体资源定期清理任务
    media_manager = container.resolve(MediaManager)
    media_manager.setup_cleanup_task(container)

    # 注册信号处理函数
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    # 阻止信号处理函数被覆盖
    signal.signal = lambda *args: None

    try:
        logger.success("Kirara AI 启动完毕，等待消息中...")
        logger.success(
            f"WebUI 管理平台本地访问地址：http://127.0.0.1:{web_server.listen_port}/"
        )
        logger.success("Application started. Waiting for events...")
        loop.create_task(check_update(container.resolve(GlobalConfig)))
        event_bus = container.resolve(EventBus)
        event_bus.post(ApplicationStarted())
        notify_extension_lifecycle(container, "startup_completed")
        loop.run_until_complete(shutdown_event.wait())
    finally:
        event_bus.post(ApplicationStopping())
        notify_extension_lifecycle(container, "shutdown_requested")

        # 停止定时任务调度器
        try:
            task_scheduler = container.resolve(TaskScheduler)
            task_scheduler.stop()
        except Exception as e:
            logger.error(f"Error stopping task scheduler: {e}")

        # 关闭记忆系统
        memory_manager = container.resolve(MemoryManager)
        logger.info("Shutting down memory system...")
        memory_manager.shutdown()

        # 关闭追踪系统
        try:
            tracing_manager = container.resolve(TracingManager)
            logger.info("Shutting down tracing system...")
            tracing_manager.shutdown()

            db_manager = container.resolve(DatabaseManager)
            logger.info("Shutting down database...")
            db_manager.shutdown()
        except Exception as e:
            logger.error(f"Error shutting down tracing system: {e}")

        # 停止Web服务器
        logger.info("Stopping web server...")

        # 停止Web服务器
        loop.run_until_complete(web_server.stop())
        logger.info("Web server terminated.")
        try:
            # 停止所有 adapter
            im_manager.stop_adapters(loop=loop)
            mcp_manager.disconnect_all_servers(loop=loop)
            # 停止插件
            plugin_loader.stop_plugins()
        except Exception as e:
            logger.error(f"Error stopping adapters: {e}")

        # 关闭事件循环
        loop.stop()
        logger.info("Application stopped gracefully")
        logger.remove()
