import shlex
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kirara_ai.llm.model_types import LLMAbility, ModelType


class IMConfig(BaseModel):
    """IM配置"""

    name: str = Field(default="", description="IM标识名称")
    enable: bool = Field(default=True, description="是否启用IM")
    adapter: str = Field(default="dummy", description="IM适配器类型")
    config: Dict[str, Any] = Field(default={}, description="IM的配置")


class ModelConfig(BaseModel):
    """模型配置"""

    id: str = Field(description="模型标识ID")
    type: str = Field(default=ModelType.LLM.value, description="模型类型：llm/embedding/image_generation等")
    ability: int = Field(description="模型能力，对应模型类型的Ability枚举值")

    model_config = ConfigDict(extra="allow")

class LLMBackendConfig(BaseModel):
    """LLM后端配置"""

    name: str = Field(description="后端标识名称")
    adapter: str = Field(description="LLM适配器类型")
    config: Dict[str, Any] = Field(default={}, description="后端配置")
    enable: bool = Field(default=True, description="是否启用")
    models: List[ModelConfig] = Field(
        default=[], description="支持的模型列表"
    )
    auto_detect_interval_days: int = Field(default=5, description="自动检测模型间隔天数，0表示禁用自动检测")
    priority: int = Field(default=100, ge=0, description="Provider 优先级，数字越小越优先")
    participate_in_failover: bool = Field(default=True, description="是否参与同模型故障转移队列")
    max_retries: int = Field(default=0, ge=0, le=10, description="Provider 内部最大重试次数")
    retry_backoff_seconds: float = Field(default=0.5, ge=0, description="重试初始退避秒数")
    retry_backoff_max_seconds: float = Field(default=5.0, ge=0, description="重试最大退避秒数")
    request_timeout_seconds: float = Field(default=60.0, gt=0, description="兼容旧配置：一次模型执行的总时间预算")
    non_stream_timeout_seconds: float = Field(default=60.0, gt=0, description="非流式请求总超时")
    stream_first_byte_timeout_seconds: float = Field(default=15.0, gt=0, description="流式请求首字节超时")
    stream_idle_timeout_seconds: float = Field(default=30.0, gt=0, description="流式请求相邻字节最大静默时间")
    circuit_failure_threshold: int = Field(default=3, ge=1, description="连续失败多少次后打开熔断")
    circuit_error_rate_threshold: float = Field(default=0.5, ge=0, le=1, description="达到最小样本后的错误率阈值")
    circuit_min_requests: int = Field(default=10, ge=1, description="错误率熔断的最小请求数")
    circuit_recovery_timeout_seconds: float = Field(default=30.0, ge=0, description="熔断打开后进入半开探测的等待时间")
    circuit_recovery_success_threshold: int = Field(default=2, ge=1, le=100, description="半开状态连续成功多少次后恢复")

    @model_validator(mode='before')
    @classmethod
    def migrate_models_format(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        自动迁移模型配置格式
        将旧格式的字符串ID列表转换为新格式的ModelConfig对象列表
        """
        if "models" in data and isinstance(data["models"], list):
            # 创建新的模型列表
            new_models = []

            for model in data["models"]:
                if isinstance(model, str):
                    # 旧格式：字符串ID，转换为ModelConfig
                    new_models.append(ModelConfig(id=model, type=ModelType.LLM.value, ability=LLMAbility.TextChat.value))
                else:
                    # 新格式或已迁移的模型配置，保持不变
                    new_models.append(model)

            data["models"] = new_models

        return data


class LLMConfig(BaseModel):
    api_backends: List[LLMBackendConfig] = Field(
        default=[], description="LLM API后端列表"
    )

class MCPTransportConfig(BaseModel):
    """CC Switch-compatible MCP server transport configuration.

    The public shape deliberately mirrors CC Switch and common MCP client
    configuration files.  Validation of required fields is performed by the
    import boundary as well, while this model remains permissive enough to
    load an old, temporarily incomplete Kirara configuration for migration.
    """

    type: str = Field(default="stdio", description="传输类型: stdio/http/sse")
    command: Optional[str] = Field(default=None, description="stdio 命令")
    args: List[str] = Field(default_factory=list, description="stdio 参数数组")
    env: Dict[str, str] = Field(default_factory=dict, description="stdio 环境变量")
    cwd: Optional[str] = Field(default=None, description="stdio 工作目录")
    url: Optional[str] = Field(default=None, description="http/sse URL")
    headers: Dict[str, str] = Field(default_factory=dict, description="http/sse 请求 Headers")

    model_config = ConfigDict(extra="allow")


class MCPAppsConfig(BaseModel):
    """CC Switch application enablement matrix."""

    claude: bool = False
    claude_desktop: bool = Field(default=False, alias="claude-desktop")
    codex: bool = False
    gemini: bool = False
    grokbuild: bool = False
    opencode: bool = False
    openclaw: bool = False
    hermes: bool = False

    model_config = ConfigDict(populate_by_name=True, extra="allow")


def _legacy_args_to_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            return shlex.split(value, posix=False)
        except ValueError:
            return value.split()
    raise TypeError("MCP args must be an array")


class MCPServerConfig(BaseModel):
    """MCP entry using the CC Switch unified structure.

    Canonical fields are ``id``, ``name``, ``server``, ``apps`` and metadata
    fields.  The compatibility properties at the bottom are intentionally
    runtime-only and keep old Kirara callers working during migration; they
    are never emitted by ``model_dump``.
    """

    id: str = Field(description="服务器标识ID")
    name: str = Field(default="", description="服务器显示名称")
    server: MCPTransportConfig = Field(default_factory=MCPTransportConfig)
    apps: MCPAppsConfig = Field(default_factory=MCPAppsConfig)
    description: str = Field(default="", description="服务器描述")
    tags: List[str] = Field(default_factory=list, description="服务器标签")
    homepage: Optional[str] = Field(default=None, description="项目主页")
    docs: Optional[str] = Field(default=None, description="文档地址")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Kirara 运行时元数据")

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_shape(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        values = dict(data)
        legacy_type = values.pop("connection_type", None)
        legacy_enable = values.pop("enable", None)

        if "name" not in values or not values.get("name"):
            values["name"] = values.get("id", "")

        if "server" not in values or values.get("server") is None:
            transport: Dict[str, Any] = {
                "type": legacy_type or values.pop("type", "stdio"),
            }
            for key in ("command", "url", "headers", "env", "cwd"):
                if key in values:
                    transport[key] = values.pop(key)
            if "args" in values:
                transport["args"] = _legacy_args_to_list(values.pop("args"))
            values["server"] = transport
        else:
            # Do not allow the old top-level fields to leak into the canonical
            # shape when a mixed config is loaded during an upgrade.
            for key in ("command", "url", "headers", "env", "cwd", "args", "type"):
                values.pop(key, None)

        metadata = dict(values.get("metadata") or {})
        if legacy_enable is not None:
            metadata["runtime_enabled"] = bool(legacy_enable)
        else:
            metadata.setdefault("runtime_enabled", True)
        values["metadata"] = metadata
        return values

    @property
    def runtime_enabled(self) -> bool:
        return bool(self.metadata.get("runtime_enabled", True))

    @runtime_enabled.setter
    def runtime_enabled(self, value: bool) -> None:
        self.metadata["runtime_enabled"] = bool(value)

    @property
    def enable(self) -> bool:
        """Legacy runtime alias; not part of the persisted public schema."""
        return self.runtime_enabled

    @enable.setter
    def enable(self, value: bool) -> None:
        self.runtime_enabled = value

    @property
    def connection_type(self) -> str:
        """Legacy alias for ``server.type``."""
        return self.server.type

    @connection_type.setter
    def connection_type(self, value: str) -> None:
        self.server.type = value

    @property
    def command(self) -> Optional[str]:
        return self.server.command

    @command.setter
    def command(self, value: Optional[str]) -> None:
        self.server.command = value

    @property
    def args(self) -> List[str]:
        return self.server.args

    @args.setter
    def args(self, value: List[str]) -> None:
        self.server.args = _legacy_args_to_list(value)

    @property
    def url(self) -> Optional[str]:
        return self.server.url

    @url.setter
    def url(self, value: Optional[str]) -> None:
        self.server.url = value

    @property
    def headers(self) -> Dict[str, str]:
        return self.server.headers

    @headers.setter
    def headers(self, value: Dict[str, str]) -> None:
        self.server.headers = value

    @property
    def env(self) -> Dict[str, str]:
        return self.server.env

    @env.setter
    def env(self, value: Dict[str, str]) -> None:
        self.server.env = value


class MCPConfig(BaseModel):
    """MCP配置"""
    servers: List[MCPServerConfig] = Field(default=[], description="MCP服务器列表")


class DefaultConfig(BaseModel):
    llm_model: str = Field(
        default="gemini-1.5-flash", description="默认使用的 LLM 模型名称"
    )


class MemoryPersistenceConfig(BaseModel):
    type: str = Field(default="file", description="持久化类型: file/redis")
    file: Dict[str, Any] = Field(
        default={"storage_dir": "./data/memory"}, description="文件持久化配置"
    )
    redis: Dict[str, Any] = Field(
        default={"host": "localhost", "port": 6379, "db": 0},
        description="Redis持久化配置",
    )


class MemoryConfig(BaseModel):
    persistence: MemoryPersistenceConfig = MemoryPersistenceConfig()
    max_entries: int = Field(default=100, description="每个作用域最大记忆条目数")
    default_scope: str = Field(default="member", description="默认作用域类型")


class WebConfig(BaseModel):
    host: str = Field(default="127.0.0.1", description="Web服务绑定的IP地址")
    port: int = Field(default=8080, description="Web服务端口号")
    secret_key: str = Field(default="", description="Web服务的密钥，用于JWT等加密")
    password_file: str = Field(
        default="web/password.hash", description="密码哈希存储路径"
    )


class PluginConfig(BaseModel):
    """插件配置"""

    enable: List[str] = Field(default=[], description="启用的外部插件列表")
    market_base_url: str = Field(
        default="https://kirara-plugin.app.lss233.com/api/v1",
        description="插件市场基础URL",
    )


class UpdateConfig(BaseModel):
    pypi_registry: str = Field(default="https://pypi.org/simple", description="PyPI 服务器 URL")
    npm_registry: str = Field(default="https://registry.npmjs.org", description="npm 服务器 URL")


class FrpcConfig(BaseModel):
    """FRPC 配置"""

    enable: bool = Field(default=False, description="是否启用 FRPC")
    server_addr: str = Field(default="", description="FRPC 服务器地址")
    server_port: int = Field(default=7000, description="FRPC 服务器端口")
    token: str = Field(default="", description="FRPC 连接令牌")
    remote_port: int = Field(default=0, description="远程端口，0 表示随机分配")


class SystemConfig(BaseModel):
    """系统配置"""

    timezone: str = Field(default="Asia/Shanghai", description="时区")


class TracingConfig(BaseModel):
    """Tracing 配置"""

    llm_tracing_content: bool = Field(default=False, description="是否记录 LLM 请求内容")

class MediaConfig(BaseModel):
    """媒体配置"""
    cleanup_duration: int = Field(default=30, description="间隔多少天清理一次媒体文件")
    auto_remove_unreferenced: bool = Field(default=True, description="是否自动删除未引用的媒体文件")
    last_cleanup_time: int = Field(default=0, description="上次清理时间")


class AgentRuntimeConfig(BaseModel):
    """统一 Agent 运行时配置。"""

    context_char_threshold: int = Field(
        default=0,
        ge=0,
        description="触发 Agent 上下文压缩的字符阈值，0 表示保持默认行为",
    )
    debug_hooks_enabled: bool = Field(
        default=True,
        description="是否注册受控的 Agent 调试 Hook Handler",
    )


class GlobalConfig(BaseModel):
    ims: List[IMConfig] = Field(default=[], description="IM配置列表")
    llms: LLMConfig = LLMConfig()
    mcp: MCPConfig = MCPConfig()
    defaults: DefaultConfig = DefaultConfig()
    memory: MemoryConfig = MemoryConfig()
    web: WebConfig = WebConfig()
    plugins: PluginConfig = PluginConfig()
    update: UpdateConfig = UpdateConfig()
    frpc: FrpcConfig = FrpcConfig()
    system: SystemConfig = SystemConfig()
    tracing: TracingConfig = TracingConfig()
    media: MediaConfig = MediaConfig()
    agent_runtime: AgentRuntimeConfig = AgentRuntimeConfig()

    model_config = ConfigDict(extra="allow")
