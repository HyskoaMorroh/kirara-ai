import shlex
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kirara_ai.llm.model_types import LLMAbility, ModelType

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    # 运行时不导入：`kirara_ai.llm.rectifier` 不该成为配置模块的启动期依赖，
    # 而 `build_rectifier_config` 里的局部导入已经够用。
    from kirara_ai.llm.rectifier import RectifierConfig


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
    stream_total_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="流式请求总超时；未显式配置时沿用 request_timeout_seconds",
    )
    circuit_failure_threshold: int = Field(default=3, ge=1, description="连续失败多少次后打开熔断")
    circuit_error_rate_threshold: float = Field(default=0.5, ge=0, le=1, description="达到最小样本后的错误率阈值")
    circuit_min_requests: int = Field(default=10, ge=1, description="错误率熔断的最小请求数")
    circuit_recovery_timeout_seconds: float = Field(default=30.0, ge=0, description="熔断打开后进入半开探测的等待时间")
    circuit_recovery_success_threshold: int = Field(default=2, ge=1, le=100, description="半开状态连续成功多少次后恢复")
    reasoning_effort: Optional[Literal["low", "medium", "high", "max"]] = Field(
        default=None,
        description=(
            "推理强度档位；留空表示沿用上游默认。"
            "各家字段不同（OpenAI reasoning_effort / Claude thinking.budget_tokens / "
            "Gemini thinkingConfig.thinkingBudget），由适配器各自翻译"
        ),
    )
    hide_ai_attribution: bool = Field(
        default=False,
        description=(
            "移除该供应商回复里的 AI 自我署名（如「作为一个 AI 助手」"
            "「本回复由 AI 生成」）。默认关闭：这是会改写模型输出的开关。"
            "只删署名句，不动答案、代码块、工具参数与用量统计"
        ),
    )
    rectifier_enabled: bool = Field(
        default=True,
        description=(
            "请求整流器总开关。上游因参数约束拒绝时，按白名单改一处再重试一次。"
            "默认开启：这几类错误不修就必然失败，而原因（思考预算与 max_tokens "
            "的关系、换模型后失效的思考签名、不支持图片的模型收到图片）"
            "既不在错误里说清，也不是用户能自己改的"
        ),
    )
    rectify_thinking_signature: bool = Field(
        default=True,
        description=(
            "整流：移除失效的思考签名后重试。"
            "多轮对话回传上一轮 thinking block 时带签名，换模型或换供应商后签名失效。"
            "只删 thinking / redacted_thinking 块与残留 signature 字段，不动正文"
        ),
    )
    rectify_thinking_budget: bool = Field(
        default=True,
        description=(
            "整流：修正思考预算与 max_tokens 的关系后重试。"
            "`adaptive` 类型不改——那是让上游自行决定预算"
        ),
    )
    rectify_media_fallback: bool = Field(
        default=True,
        description=(
            "整流：上游拒绝图片输入时，把图片块降级为可见占位文本后重试。"
            "这一项**会改变模型看到的内容**，因此可以单独关闭；"
            "换成占位而不是静默删除，是为了让模型能说「我没收到图片」"
            "而不是对着空内容编一个答案"
        ),
    )

    def build_rectifier_config(self) -> "RectifierConfig":
        """把配置映射成运行时那份 `RectifierConfig`。

        由这里统一转换，而不是让适配器各自读四个字段：开关名在两处各写一份时，
        迟早会出现「界面关了但运行时还在改」，而那种不一致没有任何症状，
        只表现为请求被静默改写。
        """
        from kirara_ai.llm.rectifier import RectifierConfig

        return RectifierConfig(
            enabled=self.rectifier_enabled,
            request_thinking_signature=self.rectify_thinking_signature,
            request_thinking_budget=self.rectify_thinking_budget,
            request_media_fallback=self.rectify_media_fallback,
        )

    @model_validator(mode="after")
    def validate_timeout_budget(self) -> "LLMBackendConfig":
        """Reject budgets whose parts cannot fit inside the total they are bounded by.

        Per-field ``gt=0`` never caught the cross-field case: a first-byte plus
        idle allowance larger than the stream's own total deadline can never be
        reached, and a retry backoff schedule longer than the non-stream total
        guarantees the last retries are dropped at the deadline instead of run.
        Both were silently accepted before and only showed up as "the timeout I
        configured does nothing".

        The check only applies to a total the user actually wrote. A total
        inherited from the legacy ``request_timeout_seconds`` is left alone so an
        existing configuration file never becomes unloadable after an upgrade;
        the runtime already clamps activity waits to the deadline in that case.
        """
        if self.retry_backoff_max_seconds < self.retry_backoff_seconds:
            raise ValueError(
                "retry_backoff_max_seconds cannot be smaller than retry_backoff_seconds"
            )

        if "stream_total_timeout_seconds" in self.model_fields_set:
            stream_activity = (
                self.stream_first_byte_timeout_seconds + self.stream_idle_timeout_seconds
            )
            if stream_activity > self.stream_total_timeout_seconds:
                raise ValueError(
                    "stream_first_byte_timeout_seconds + stream_idle_timeout_seconds "
                    f"({stream_activity}) exceeds stream_total_timeout_seconds "
                    f"({self.stream_total_timeout_seconds})"
                )

        if self.max_retries and "non_stream_timeout_seconds" in self.model_fields_set:
            # Backoff grows geometrically but is clamped by retry_backoff_max_seconds.
            backoff_budget = 0.0
            delay = self.retry_backoff_seconds
            for _ in range(self.max_retries):
                backoff_budget += min(delay, self.retry_backoff_max_seconds)
                delay *= 2
            if backoff_budget > self.non_stream_timeout_seconds:
                raise ValueError(
                    f"retry backoff budget ({backoff_budget}) exceeds "
                    f"non_stream_timeout_seconds ({self.non_stream_timeout_seconds})"
                )
        return self

    def effective_stream_total_timeout(self) -> float:
        """Return the stream deadline, honoring the legacy key when the new one is unset."""
        if "stream_total_timeout_seconds" in self.model_fields_set:
            return self.stream_total_timeout_seconds
        return self.request_timeout_seconds

    def effective_non_stream_timeout(self) -> float:
        """Return the non-stream deadline, honoring the legacy key when the new one is unset."""
        if "non_stream_timeout_seconds" in self.model_fields_set:
            return self.non_stream_timeout_seconds
        return self.request_timeout_seconds

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
    roots: List[str] = Field(
        default_factory=list,
        description=(
            "显式允许 MCP 服务访问的本地文件根目录或文件路径；"
            "未配置时不声明 roots 能力"
        ),
    )
    startup_timeout_ms: int = Field(
        default=120_000,
        ge=1_000,
        le=600_000,
        description="启动 MCP 进程并完成 initialize 的最大等待时间（毫秒）",
    )

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
    disable_auto_check: bool = Field(
        default=False,
        description=(
            "禁用启动时的自动版本检查。"
            "离线或内网部署既查不到注册表又要等超时；关闭后完全不发起请求，"
            "WebUI 的「检查更新」按钮仍然可用"
        ),
    )


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


class CreatorChannelIdentity(BaseModel):
    """一个被声明为「项目创建者本人」的 IM 渠道身份。

    需求 10 要求只有创建者能通过插件改服务器内容，其他人收到这类指令一律忽视
    但仍得到正常回复。后半句一直成立；前半句在 IM 渠道上此前**无法成立**——
    `principal_can_control_agent` 是唯一门禁，而 principal 只由 HTTP Bearer
    中间件注入，IM 入站链路全程没有它。结果不是「非创建者不行」，
    而是「所有人都不行」，包括创建者本人：MCP 工具列表恒为空、command Hook 恒被拒。

    这份声明就是那座桥。三条刻意的设计：

    - **默认空表**。不声明任何身份时行为与此前逐字节一致，
      不存在「升级之后聊天里突然能动服务器」这种事。
    - **渠道与发送者一起比**。QQ 号和 Telegram 用户 ID 可能撞号，
      只比一个等于把另一个渠道的同号用户也放进来。
    - **群聊默认不生效**。群里所有人都看得到创建者发的指令并照抄；
      虽然照抄的人 `sender_scope` 不同因而拿不到身份，但把宿主操作暴露在
      多人可见的会话里是另一回事，要开必须显式写 `allow_group_chat`。
    """

    channel_type: str = Field(
        description="渠道类型：webui / onebot / qqbot / telegram / wecom",
    )
    sender_scope: str = Field(
        description="该渠道上创建者的用户标识（QQ 号、Telegram 用户 ID 等）",
    )
    account_scope: Optional[str] = Field(
        default=None,
        description=(
            "可选。限定只有经由这个机器人账号收到的消息才算创建者，"
            "留空表示该渠道下的任意账号。"
        ),
    )
    adapter_instance: Optional[str] = Field(
        default=None,
        description="可选。限定适配器实例名，留空表示任意实例。",
    )
    allow_group_chat: bool = Field(
        default=False,
        description=(
            "是否允许在群聊里生效。默认关闭：群聊对所有成员可见，"
            "把宿主操作暴露在那里的风险与私聊完全不同。"
        ),
    )

    @field_validator("channel_type", "sender_scope")
    @classmethod
    def _require_non_empty(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            # 空的 sender_scope 会匹配到 ChannelContext 的兜底值
            # （`unknown-sender`），等于对所有取不到用户 ID 的消息放开。
            raise ValueError("channel_type 与 sender_scope 不能为空")
        return text

    @field_validator("account_scope", "adapter_instance")
    @classmethod
    def _normalize_optional(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


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
    reply_stream_mode: Literal["off", "aggregate"] = Field(
        default="off",
        description=(
            "回复生成模式。off：非流式请求（默认，与既有行为一致）；"
            "aggregate：以流式方式向上游取回内容再整段投递，"
            "从而让流式首字节超时、静默超时与首字节前的故障转移真正生效。"
            "IM 平台普遍不支持逐字编辑消息，因此这里不做逐字推送。"
        ),
    )
    turn_deadline_seconds: float = Field(
        default=0.0,
        ge=0,
        le=3600,
        description=(
            "单轮对话的总时间预算（秒），0 表示不设总预算。"
            "设置后会作为 deadline 与取消信号一并下传给模型调用："
            "超时后正在等待的上游请求会被取消，而不是让这一轮无限悬挂。"
            "注意它约束的是整轮（含多次工具调用），"
            "单次请求的超时仍由各 Provider 自己的 timeout 字段决定。"
        ),
    )
    creator_channel_identities: List[CreatorChannelIdentity] = Field(
        default_factory=list,
        description=(
            "声明哪些 IM 渠道身份属于项目创建者。"
            "默认为空：不声明时聊天侧拿不到任何身份，"
            "MCP 工具与 command Hook 在 IM 渠道上一律不可用（与旧行为一致）。"
            "声明后，来自这些身份的消息会带上创建者 principal，"
            "从而能像 WebUI 一样使用受保护的插件能力。"
        ),
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
