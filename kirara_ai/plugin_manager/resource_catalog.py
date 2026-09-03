"""Unified resource catalog for discover-and-install flows.

The catalog is deliberately small and server-owned.  Search results are
metadata only; installation always resolves a server-generated catalog ID and
creates the same verified resource package used by offline imports.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any, Mapping

from packaging.version import Version

from kirara_ai.logger import get_logger

from .resource_lifecycle import ResourceLifecycleService, ResourceStateError
from .resource_sources import ResourceSourceService
from .system_dependencies import SystemDependencyService, dependency_ids_for_resource

logger = get_logger("ResourceCatalog")


OFFICE_RESEARCH_PROMPT = """我是上班族，偏学术研究。我经常要处理【邮件、会议、表格、文档等】。我偶尔兼职程序员。
帮我干活时：

- 用大白话讲清结果，内容要精湛精美；
- 该提醒就直接提醒，不要只顺着我；
- 若出现敏感词或sensitive words，请你按相同含义更换成符合要求的内容再重新执行，不要中断；
- 除了真正代码，不用代码块堆正文；
- 写邮件和报告时去掉空话、套话、过多破折号和生硬AI腔；
- 不在结果里暴露身份号码、电话、住址、付款信息或密钥；
- 涉及发送、退订、建会、删除、发布、付款时先停下来让我确认。
"""

RESEARCH_MEMORY_POLICY = """这是研究型 Agent 的记忆使用边界。

- 只把当前渠道、账号、会话和 Agent 身份范围内的内容作为上下文。
- 先区分已确认事实、用户偏好和待核实线索，不把推测写成事实。
- 需要跨渠道或跨会话复用信息时，必须由用户明确提出，不能因为标识相同而自动合并。
- 记忆内容只用于辅助当前任务；遇到发送、退订、建会、删除、发布或付款等操作，仍须先请求用户确认。
"""


_BUILTINS: tuple[dict[str, Any], ...] = (
    {
        "catalog_id": "prompt:office-research",
        "type": "prompt",
        "name": "Office and Research Assistant",
        "description": "办公、邮件、会议、表格和学术研究场景的中文行为提示词。",
        "version": "1.0.1",
        "permissions": ["workflow.read"],
        "entry": "PROMPT.md",
        "source": "catalog://kirara/prompt/office-research",
        "tags": ["office", "research", "chinese"],
        "content": OFFICE_RESEARCH_PROMPT,
    },
    {
        "catalog_id": "mcp:context7",
        "type": "mcp",
        "name": "Context7",
        "description": "通过 MCP 获取最新软件库和框架文档，用于 AI 功能调试。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/context7",
        "tags": ["documentation", "debugging", "stdio"],
        # 与其余 stdio 预设同一条纪律：说清靠什么拉起。
        # context7 是一个 npm 包，运行时镜像不装 Node——缺这条声明时，
        # 用户点「启用」只会得到「连接失败 / 已连接 0 / 工具数 0」，
        # 而界面上没有任何线索指向真正的原因。
        "runtime_dependency": "npx",
        "content": {
            "id": "context7",
            "name": "Context7",
            "server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@upstash/context7-mcp"],
                "env": {},
            },
            "apps": {
                "claude": False,
                "claude-desktop": False,
                "codex": True,
                "gemini": False,
                "grokbuild": False,
                "opencode": False,
                "openclaw": False,
                "hermes": False,
            },
            "description": "Context7 documentation lookup MCP server",
            "tags": ["documentation", "debugging"],
            "homepage": "https://context7.com",
            "docs": "https://context7.com/docs",
            "metadata": {"catalog_id": "mcp:context7", "managed": True},
        },
    },
    {
        "catalog_id": "memory:research-context",
        "type": "memory",
        "name": "Research Context Memory",
        "description": "研究型 Agent 的记忆边界与事实核验策略，可绑定到指定 Agent。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "MEMORY.md",
        "source": "catalog://kirara/memory/research-context",
        "tags": ["memory", "research", "privacy", "isolation"],
        "content": RESEARCH_MEMORY_POLICY,
    },
    {
        "catalog_id": "hook:ai-debug",
        "type": "hook",
        "name": "AI Debug Audit Hooks",
        "description": "记录 Agent 生命周期和工具策略事件的受控 Hook 声明。",
        # 事件集合变了必须抬版本号：`install()` 只在 bundled > installed 时推进
        # 已装的资源，不抬的后果是「新部署有、老部署没有」，而两边界面都显示已安装。
        "version": "1.2.0",
        "permissions": ["workflow.read", "process.execute"],
        "entry": "hook.json",
        "source": "catalog://kirara/hook/ai-debug",
        "tags": ["debugging", "audit", "hooks"],
        "content": {
            "events": {
                "SessionStart": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "SessionStart"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "UserPromptSubmit": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "UserPromptSubmit"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "PreToolUse": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "PreToolUse"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "PermissionRequest": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "PermissionRequest"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "PostToolUse": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "PostToolUse"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "PreCompact": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "PreCompact"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "PostCompact": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "PostCompact"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "Stop": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "Stop"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                # 这三个事件是后补的派发点（会话清理、队友委派前后）。
                # 内置件必须跟上：它是「Hook 到底有没有在跑」的唯一现成样本，
                # 少一个事件就等于那类事件在产品上没有可验证的入口——
                # 用户照这份声明抄，抄到的里面压根没有它。
                "SessionEnd": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "SessionEnd"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "SubagentStart": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "SubagentStart"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "SubagentStop": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "SubagentStop"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
            }
        },
    },
    {
        "catalog_id": "mcp:fetch",
        "type": "mcp",
        "name": "Fetch",
        "description": "抓取网页并转成适合模型阅读的文本，用于让 AI 读取在线内容。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/fetch",
        "tags": ["web", "fetch", "stdio"],
        # 这台机器上靠什么把它拉起来。
        #
        # **预置一个模板不等于那个 MCP 能跑起来**：`uvx` 与 `npx` 都不是本项目的
        # 依赖，运行时镜像两个都没装。界面据此显示「模板已填好，但这台机器缺 uvx」,
        # 而不是让用户点了启用之后看到一个连不上的服务器——现场那句
        # 「连接失败 / 已连接 0 / 工具数 0」正是缺这条说明的形态。
        "runtime_dependency": "uvx",
        "content": {
            "id": "fetch",
            "name": "Fetch",
            "server": {
                "type": "stdio",
                "command": "uvx",
                "args": ["mcp-server-fetch"],
                # env 一律留空：模板会被写进 `data/resources/` 并可能随备份导出，
                # 预填一个 token 会跟着走。需要密钥的服务器在描述里说明要填什么。
                "env": {},
            },
            "apps": {
                "claude": False,
                "claude-desktop": False,
                "codex": True,
                "gemini": False,
                "grokbuild": False,
                "opencode": False,
                "openclaw": False,
                "hermes": False,
            },
            "description": "抓取网页并转成适合模型阅读的文本，用于让 AI 读取在线内容。",
            "tags": ["web", "fetch", "stdio"],
            "homepage": "https://github.com/modelcontextprotocol/servers",
            "metadata": {"catalog_id": "mcp:fetch", "managed": True},
        },
    },
    {
        "catalog_id": "mcp:time",
        "type": "mcp",
        "name": "Time",
        "description": "提供当前时间与时区换算，避免模型凭训练数据猜测日期。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/time",
        "tags": ["time", "timezone", "stdio"],
        # 这台机器上靠什么把它拉起来。
        #
        # **预置一个模板不等于那个 MCP 能跑起来**：`uvx` 与 `npx` 都不是本项目的
        # 依赖，运行时镜像两个都没装。界面据此显示「模板已填好，但这台机器缺 uvx」,
        # 而不是让用户点了启用之后看到一个连不上的服务器——现场那句
        # 「连接失败 / 已连接 0 / 工具数 0」正是缺这条说明的形态。
        "runtime_dependency": "uvx",
        "content": {
            "id": "time",
            "name": "Time",
            "server": {
                "type": "stdio",
                "command": "uvx",
                "args": ["mcp-server-time"],
                # env 一律留空：模板会被写进 `data/resources/` 并可能随备份导出，
                # 预填一个 token 会跟着走。需要密钥的服务器在描述里说明要填什么。
                "env": {},
            },
            "apps": {
                "claude": False,
                "claude-desktop": False,
                "codex": True,
                "gemini": False,
                "grokbuild": False,
                "opencode": False,
                "openclaw": False,
                "hermes": False,
            },
            "description": "提供当前时间与时区换算，避免模型凭训练数据猜测日期。",
            "tags": ["time", "timezone", "stdio"],
            "homepage": "https://github.com/modelcontextprotocol/servers",
            "metadata": {"catalog_id": "mcp:time", "managed": True},
        },
    },
    {
        "catalog_id": "mcp:memory",
        "type": "mcp",
        "name": "Knowledge Graph Memory",
        "description": "以知识图谱形式保存与检索长期事实，跨会话可用。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/memory",
        "tags": ["memory", "knowledge-graph", "stdio"],
        # 这台机器上靠什么把它拉起来。
        #
        # **预置一个模板不等于那个 MCP 能跑起来**：`uvx` 与 `npx` 都不是本项目的
        # 依赖，运行时镜像两个都没装。界面据此显示「模板已填好，但这台机器缺 npx」,
        # 而不是让用户点了启用之后看到一个连不上的服务器——现场那句
        # 「连接失败 / 已连接 0 / 工具数 0」正是缺这条说明的形态。
        "runtime_dependency": "npx",
        "content": {
            "id": "memory",
            "name": "Knowledge Graph Memory",
            "server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-memory"],
                # env 一律留空：模板会被写进 `data/resources/` 并可能随备份导出，
                # 预填一个 token 会跟着走。需要密钥的服务器在描述里说明要填什么。
                "env": {},
            },
            "apps": {
                "claude": False,
                "claude-desktop": False,
                "codex": True,
                "gemini": False,
                "grokbuild": False,
                "opencode": False,
                "openclaw": False,
                "hermes": False,
            },
            "description": "以知识图谱形式保存与检索长期事实，跨会话可用。",
            "tags": ["memory", "knowledge-graph", "stdio"],
            "homepage": "https://github.com/modelcontextprotocol/servers",
            "metadata": {"catalog_id": "mcp:memory", "managed": True},
        },
    },
    {
        "catalog_id": "mcp:sequential-thinking",
        "type": "mcp",
        "name": "Sequential Thinking",
        "description": "把复杂问题拆成可回溯的思考步骤，用于多步推理调试。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/sequential-thinking",
        "tags": ["reasoning", "debugging", "stdio"],
        # 这台机器上靠什么把它拉起来。
        #
        # **预置一个模板不等于那个 MCP 能跑起来**：`uvx` 与 `npx` 都不是本项目的
        # 依赖，运行时镜像两个都没装。界面据此显示「模板已填好，但这台机器缺 npx」,
        # 而不是让用户点了启用之后看到一个连不上的服务器——现场那句
        # 「连接失败 / 已连接 0 / 工具数 0」正是缺这条说明的形态。
        "runtime_dependency": "npx",
        "content": {
            "id": "sequential-thinking",
            "name": "Sequential Thinking",
            "server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
                # env 一律留空：模板会被写进 `data/resources/` 并可能随备份导出，
                # 预填一个 token 会跟着走。需要密钥的服务器在描述里说明要填什么。
                "env": {},
            },
            "apps": {
                "claude": False,
                "claude-desktop": False,
                "codex": True,
                "gemini": False,
                "grokbuild": False,
                "opencode": False,
                "openclaw": False,
                "hermes": False,
            },
            "description": "把复杂问题拆成可回溯的思考步骤，用于多步推理调试。",
            "tags": ["reasoning", "debugging", "stdio"],
            "homepage": "https://github.com/modelcontextprotocol/servers",
            "metadata": {"catalog_id": "mcp:sequential-thinking", "managed": True},
        },
    },
    {
        "catalog_id": "mcp:filesystem",
        "type": "mcp",
        "name": "Filesystem",
        "description": "读写指定目录下的文件。启用前必须在 args 末尾追加允许访问的目录。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/filesystem",
        "tags": ["filesystem", "files", "stdio"],
        # 这台机器上靠什么把它拉起来。
        #
        # **预置一个模板不等于那个 MCP 能跑起来**：`uvx` 与 `npx` 都不是本项目的
        # 依赖，运行时镜像两个都没装。界面据此显示「模板已填好，但这台机器缺 npx」,
        # 而不是让用户点了启用之后看到一个连不上的服务器——现场那句
        # 「连接失败 / 已连接 0 / 工具数 0」正是缺这条说明的形态。
        "runtime_dependency": "npx",
        "content": {
            "id": "filesystem",
            "name": "Filesystem",
            "server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                # env 一律留空：模板会被写进 `data/resources/` 并可能随备份导出，
                # 预填一个 token 会跟着走。需要密钥的服务器在描述里说明要填什么。
                "env": {},
            },
            "apps": {
                "claude": False,
                "claude-desktop": False,
                "codex": True,
                "gemini": False,
                "grokbuild": False,
                "opencode": False,
                "openclaw": False,
                "hermes": False,
            },
            "description": "读写指定目录下的文件。启用前必须在 args 末尾追加允许访问的目录。",
            "tags": ["filesystem", "files", "stdio"],
            "homepage": "https://github.com/modelcontextprotocol/servers",
            "metadata": {"catalog_id": "mcp:filesystem", "managed": True},
        },
    },
    {
        "catalog_id": "mcp:chrome-devtools",
        "type": "mcp",
        "name": "Chrome DevTools",
        "description": "驱动 Chrome 检查 DOM、控制台与网络请求，用于前端调试。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/chrome-devtools",
        "tags": ["browser", "devtools", "stdio"],
        # 这台机器上靠什么把它拉起来。
        #
        # **预置一个模板不等于那个 MCP 能跑起来**：`uvx` 与 `npx` 都不是本项目的
        # 依赖，运行时镜像两个都没装。界面据此显示「模板已填好，但这台机器缺 npx」,
        # 而不是让用户点了启用之后看到一个连不上的服务器——现场那句
        # 「连接失败 / 已连接 0 / 工具数 0」正是缺这条说明的形态。
        "runtime_dependency": "npx",
        "content": {
            "id": "chrome-devtools",
            "name": "Chrome DevTools",
            "server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "chrome-devtools-mcp@latest"],
                # env 一律留空：模板会被写进 `data/resources/` 并可能随备份导出，
                # 预填一个 token 会跟着走。需要密钥的服务器在描述里说明要填什么。
                "env": {},
            },
            "apps": {
                "claude": False,
                "claude-desktop": False,
                "codex": True,
                "gemini": False,
                "grokbuild": False,
                "opencode": False,
                "openclaw": False,
                "hermes": False,
            },
            "description": "驱动 Chrome 检查 DOM、控制台与网络请求，用于前端调试。",
            "tags": ["browser", "devtools", "stdio"],
            "homepage": "https://github.com/ChromeDevTools/chrome-devtools-mcp",
            "metadata": {"catalog_id": "mcp:chrome-devtools", "managed": True},
        },
    },
    {
        "catalog_id": "mcp:playwright",
        "type": "mcp",
        "name": "Playwright",
        "description": "以可访问性树驱动浏览器，完成导航、填表与截图。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/playwright",
        "tags": ["browser", "automation", "stdio"],
        # 这台机器上靠什么把它拉起来。
        #
        # **预置一个模板不等于那个 MCP 能跑起来**：`uvx` 与 `npx` 都不是本项目的
        # 依赖，运行时镜像两个都没装。界面据此显示「模板已填好，但这台机器缺 npx」,
        # 而不是让用户点了启用之后看到一个连不上的服务器——现场那句
        # 「连接失败 / 已连接 0 / 工具数 0」正是缺这条说明的形态。
        "runtime_dependency": "npx",
        "content": {
            "id": "playwright",
            "name": "Playwright",
            "server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@playwright/mcp@latest"],
                # env 一律留空：模板会被写进 `data/resources/` 并可能随备份导出，
                # 预填一个 token 会跟着走。需要密钥的服务器在描述里说明要填什么。
                "env": {},
            },
            "apps": {
                "claude": False,
                "claude-desktop": False,
                "codex": True,
                "gemini": False,
                "grokbuild": False,
                "opencode": False,
                "openclaw": False,
                "hermes": False,
            },
            "description": "以可访问性树驱动浏览器，完成导航、填表与截图。",
            "tags": ["browser", "automation", "stdio"],
            "homepage": "https://github.com/microsoft/playwright-mcp",
            "metadata": {"catalog_id": "mcp:playwright", "managed": True},
        },
    },
    {
        "catalog_id": "skill:agent-browser",
        "type": "skill",
        "name": "Agent Browser",
        "description": "浏览器自动化技能：导航、填表、点击、截图与数据提取。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "https://github.com/vercel-labs/agent-browser",
        # skill 不走内置文件写入，而是从 GitHub 真实下载（见 `install()`）。
        # 格式必须是 `owner/repo:directory`——`install()` 直接 split，
        # 格式不对会在用户点了安装之后才抛 ValueError。
        "source_key": "vercel-labs/agent-browser:skills/agent-browser",
        "tags": ["browser", "automation", "skill"],
        "installs": 763401,
    },
)



class ResourceCatalogError(ResourceStateError):
    """A catalog coordinate is missing or cannot be installed."""


class ResourceCatalogService:
    """Search and install typed resources through one stable contract."""

    def __init__(
        self,
        lifecycle: ResourceLifecycleService,
        sources: ResourceSourceService | None = None,
        dependencies: SystemDependencyService | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.sources = sources or ResourceSourceService(lifecycle)
        self.dependencies = dependencies

    def search(
        self,
        resource_type: str | None,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        if resource_type is not None and resource_type not in {"prompt", "skill", "memory", "mcp", "hook"}:
            raise ResourceCatalogError("catalog resource type is not supported")
        if not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ResourceCatalogError("catalog search limit is outside the allowed range")
        if not isinstance(offset, int) or offset < 0:
            raise ResourceCatalogError("catalog search offset is invalid")
        query = str(query or "").strip()
        if len(query) > 120:
            raise ResourceCatalogError("catalog search query is invalid")

        records = [self._public_builtin(item) for item in _BUILTINS]
        remote_status: dict[str, Any] = {
            "provider": "skills.sh",
            "status": "not_requested",
            "error": None,
            "total_count": None,
        }
        if resource_type in (None, "skill"):
            # skills.sh is the online Skill index.  A failed remote index does
            # not hide local catalog entries or make the catalog unusable.
            if query:
                try:
                    # The remote index has already applied its requested
                    # offset; do not apply that page offset a second time
                    # after merging catalog metadata.
                    skill_results = self.sources.search_skills(query, limit=limit, offset=offset)
                    records.extend(self._skill_record(item) for item in skill_results["skills"])
                    remote_status.update(
                        {
                            "status": "ok",
                            "total_count": skill_results.get("total_count", 0),
                        }
                    )
                except Exception:
                    remote_status.update(
                        {
                            "status": "error",
                            "error": "skills.sh 在线索引暂时不可用",
                        }
                    )
        filtered = [
            item
            for item in records
            if (resource_type is None or item["type"] == resource_type)
            and self._matches(item, query)
        ]
        unique: dict[str, dict[str, Any]] = {item["catalog_id"]: item for item in filtered}
        ordered = [self._with_install_state(item) for item in unique.values()]
        # A successful skills.sh response is already offset-applied.  Keep its
        # page intact; only local-only searches use the catalog slice here.
        page = (
            ordered[:limit]
            if remote_status["status"] == "ok"
            else ordered[offset : offset + limit]
        )
        total_count = len(ordered)
        if remote_status["status"] == "ok":
            try:
                total_count = max(total_count, int(remote_status["total_count"] or 0))
            except (TypeError, ValueError):
                pass
        return {
            "query": query,
            "type": resource_type,
            "items": page,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "remote": remote_status,
        }

    def get(self, catalog_id: str) -> dict[str, Any]:
        item = self._find(catalog_id)
        if "content" in item:
            item = self._public_builtin(item)
        return self._with_install_state(item)

    def install(self, catalog_id: str, *, branch: str | None = None) -> dict[str, Any]:
        item = self._find(catalog_id)
        existing = self._installed_for_catalog(item)
        if existing is not None:
            if item["type"] != "skill" and Version(str(item["version"])) > Version(
                str(existing["current_version"])
            ):
                return self._backfilled(item, self._install_builtin(item, update=True))
            return self._backfilled(item, existing)
        if item["type"] == "skill":
            source_key = str(item["source_key"])
            owner_repo, directory = source_key.split(":", 1)
            owner, name = owner_repo.split("/", 1)
            return self._backfilled(
                item,
                self.sources.install_skill(
                    owner=owner,
                    name=name,
                    branch=branch,
                    directory=directory,
                    source_key=source_key,
                ),
            )
        return self._backfilled(item, self._install_builtin(item))

    def _backfilled(
        self, item: Mapping[str, Any], resource: Mapping[str, Any]
    ) -> dict[str, Any]:
        """给缺显示名/描述的资源补上目录里那一份，**只补空缺**。

        为什么不只在新安装时写：修复之前装好的资源，其 `source_metadata` 里
        没有名称——而在真实部署里那些恰恰是绝大多数。它们不会因为代码更新
        自己长出名字，于是「按名称搜索」对老资源继续失效。这里在每次
        `install()`（含启动时的 `ensure_builtins()`）顺带补齐。

        走 `set_display_metadata` 而不是重装或抬版本：显示名不参与
        `content_sha256`，为补一行字给资源升一个版本会在版本列表里留下一条
        与内容无关的记录，并触发一次多余的备份。

        **只补空缺**：用户可能已经把一条资源改成了自己的叫法，
        用目录里的名字盖掉它等于每次启动都撤销一次用户的重命名。
        """

        patch: dict[str, Any] = {}
        for key in ("name", "description"):
            if resource.get(key):
                continue
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                patch[key] = value
        if not patch:
            return dict(resource)
        resource_id = str(resource.get("resource_id") or "")
        try:
            return self.lifecycle.set_display_metadata(resource_id, **patch)
        except Exception as error:  # noqa: BLE001 - 补显示名失败不该让安装失败
            logger.warning("资源 %s 的显示名补齐失败（不影响安装）：%s", resource_id, error)
            return dict(resource)

    def ensure_builtins(self) -> None:
        """Install or safely advance built-ins to the bundled version.

        这个方法跑在启动路径上（`entry.py`），因此它对失败的处理是**降级而非抛出**：
        目录里的 skill 条目要出网到 GitHub 才能装，而「预置一条可选技能」不该让
        「服务能不能启动」取决于 github.com 此刻可不可达。离线部署、公司代理、
        上游抽风都会走到这里。跳过的条目记进 `_builtin_skips`，界面据此说明
        「这条内置还没装上，原因是 X」，而不是让它静默消失。
        """

        skips: list[dict[str, str]] = []
        for item in _BUILTINS:
            catalog_id = str(item["catalog_id"])
            try:
                self.install(catalog_id)
            except Exception as error:  # noqa: BLE001 - 启动路径只降级，不阻断
                skips.append({"catalog_id": catalog_id, "reason": str(error) or type(error).__name__})
                logger.warning(
                    "内置资源 %s 预置失败，已跳过（不影响启动）：%s", catalog_id, error
                )
        self._builtin_skips = skips

    def builtin_provisioning_report(self) -> list[dict[str, str]]:
        """返回上一次 `ensure_builtins()` 跳过的内置条目及原因。

        只读快照。没跑过 `ensure_builtins()` 时是空列表——那与「全部装成功」
        在这个返回值上同形，判断「是否装全」要看资源列表，不要看这里为空。
        """

        return [dict(entry) for entry in getattr(self, "_builtin_skips", ())]

    def project_dependencies(self, item: Mapping[str, Any]) -> dict[str, Any]:
        """Project persisted VPS readiness without probing or mutating resources."""

        result = dict(item)
        dependency_ids = self._dependency_ids(item)
        system_dependencies = (
            [self.dependencies.get_dependency(dependency_id) for dependency_id in dependency_ids]
            if self.dependencies is not None
            else []
        )
        result.update(
            {
                "dependency_ids": dependency_ids,
                "system_dependencies": system_dependencies,
                "dependencies_ready": not dependency_ids
                or (
                    len(system_dependencies) == len(dependency_ids)
                    and all(dependency.get("ready") is True for dependency in system_dependencies)
                ),
                "dependency_status": self._dependency_status(
                    dependency_ids, system_dependencies
                ),
            }
        )
        return result

    def _install_builtin(
        self, item: Mapping[str, Any], *, update: bool = False
    ) -> dict[str, Any]:
        content = item["content"]
        if isinstance(content, str):
            data = content.encode("utf-8")
        else:
            data = (json.dumps(content, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        files = {str(item["entry"]): data}
        records = [
            {"path": path, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            for path, value in files.items()
        ]
        content_hash = hashlib.sha256(
            b"".join(f"{record['path']}:{record['size']}:{record['sha256']}\n".encode("ascii") for record in records)
        ).hexdigest()
        resource_id = str(item["catalog_id"]).replace(":", ".", 1)
        manifest = {
            "resource_id": resource_id,
            "type": item["type"],
            "version": item["version"],
            "source": item["source"],
            "source_key": item["catalog_id"],
            "source_metadata": {
                "provider": "catalog",
                "catalog_id": item["catalog_id"],
                "tags": item["tags"],
                # 目录条目自己就带名称与描述（界面上「Office and Research Assistant」
                # 与那句中文说明都出自这里）。此前建 manifest 时把它们丢掉了，
                # 于是装完的资源在列表里只有一个 ID，搜索框承诺的「按名称、描述检索」
                # 对目录安装的资源永远命中不了。
                "name": item.get("name"),
                "description": item.get("description"),
            },
            "entry": item["entry"],
            "permissions": item["permissions"],
            "files": records,
            "content_sha256": content_hash,
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            for path, value in files.items():
                output.writestr(path, value)
        temporary = self.lifecycle.imports_path / f"catalog-{hashlib.sha256(archive.getvalue()).hexdigest()}.zip"
        temporary.write_bytes(archive.getvalue())
        try:
            if update:
                return self.lifecycle.update_archive(
                    temporary, expected_resource_id=resource_id
                )
            return self.lifecycle.install_archive(temporary)
        finally:
            temporary.unlink(missing_ok=True)

    def _find(self, catalog_id: str) -> dict[str, Any]:
        for item in _BUILTINS:
            if item["catalog_id"] == catalog_id:
                return dict(item)
        if catalog_id.startswith("skill:"):
            source_key = catalog_id.removeprefix("skill:")
            if ":" not in source_key:
                raise ResourceCatalogError("catalog Skill identity is invalid")
            owner_repo, directory = source_key.split(":", 1)
            if "/" not in owner_repo:
                raise ResourceCatalogError("catalog Skill identity is invalid")
            owner, repository = owner_repo.split("/", 1)
            self.sources.validate_repository(owner, repository, "main")
            directory = self.sources._validate_directory(directory)
            return {
                "catalog_id": catalog_id,
                "type": "skill",
                "name": directory.rsplit("/", 1)[-1],
                "description": "GitHub Skill resource",
                "source_key": self.sources.source_key(owner, repository, directory),
                "owner": owner,
                "repository": repository,
                "branch": None,
                "directory": directory,
                "source_url": self.sources._skill_source_url(
                    owner, repository, None, directory
                ),
            }
        raise ResourceCatalogError("catalog item is not available")

    @staticmethod
    def _skill_coordinates(item: Mapping[str, Any]) -> dict[str, Any]:
        """把 skill 条目的 owner / repository / directory 归一成同一种形状。

        内置条目只声明 `source_key`（`owner/repo:directory`），而 `_find()` 为
        在线搜索结果补出的条目声明的是拆开的四个键。`_installed_for_catalog`
        原先只读拆开的那四个键，于是内置 skill 永远匹配不上已装资源——每次启动
        都重新下载一遍，第二次撞「resource ID is already installed」。
        这里补齐缺失的那半边，两种来源走同一条比对。
        """

        coordinates = {
            key: item.get(key) for key in ("owner", "repository", "branch", "directory")
        }
        if coordinates["owner"] and coordinates["repository"]:
            return coordinates
        source_key = item.get("source_key")
        if not isinstance(source_key, str) or ":" not in source_key:
            return coordinates
        owner_repo, _, directory = source_key.partition(":")
        if "/" not in owner_repo:
            return coordinates
        owner, repository = owner_repo.split("/", 1)
        coordinates["owner"] = coordinates["owner"] or owner
        coordinates["repository"] = coordinates["repository"] or repository
        coordinates["directory"] = coordinates["directory"] or directory
        return coordinates

    def _installed_for_catalog(self, item: Mapping[str, Any]) -> dict[str, Any] | None:
        source_key = item.get("catalog_id") or item.get("source_key")
        candidates: list[dict[str, Any]] = []
        coordinates = (
            self._skill_coordinates(item) if item.get("type") == "skill" else {}
        )
        for resource in self.lifecycle.list_resources():
            if resource.get("source_key") == source_key:
                return resource
            metadata = resource.get("source_metadata") or {}
            if metadata.get("catalog_id") == source_key:
                return resource
            if item.get("type") != "skill" or resource.get("type") != "skill":
                continue
            if metadata.get("provider") != "github":
                continue
            if any(
                not isinstance(coordinates.get(key), str)
                or metadata.get(key) != coordinates.get(key)
                for key in ("owner", "repository")
            ):
                continue
            requested_branch = coordinates.get("branch")
            if requested_branch and metadata.get("branch") != requested_branch:
                continue
            requested_directory = str(coordinates.get("directory") or "").strip("/")
            installed_directory = str(metadata.get("directory") or "").strip("/")
            if not requested_directory or not installed_directory:
                continue
            if requested_directory == installed_directory:
                return resource
            # 上游把 Skill 放在仓库根时，`_fetch_skill_files` 解析出的目录是
            # `REPOSITORY_ROOT_MARKER`（`"."`），与请求目录必然不同字符串。
            # 同一个 owner/repo 下这类资源只会有一份，所以按仓库归属认定即可。
            if installed_directory == ResourceSourceService.REPOSITORY_ROOT_MARKER:
                candidates.append(resource)
                continue
            if (
                requested_directory.rsplit("/", 1)[-1].casefold()
                == installed_directory.rsplit("/", 1)[-1].casefold()
            ):
                candidates.append(resource)
        return candidates[0] if len(candidates) == 1 else None

    def _with_install_state(self, item: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(item)
        installed = self._installed_for_catalog(item)
        result["installed"] = installed is not None
        result["installed_resource_id"] = installed.get("resource_id") if installed else None
        result["enabled"] = bool(installed and installed.get("enabled"))
        return self.project_dependencies(result)

    @staticmethod
    def _dependency_ids(item: Mapping[str, Any]) -> list[str]:
        """委派给 `dependency_ids_for_resource`。

        映射本身已移到 `system_dependencies`：Agent 运行时也要用同一份判断，
        两处各写一份会各自漂移，而那种不一致没有症状——界面说已就绪、
        运行时说缺失（或反过来），模型只会照着一份它执行不了的说明自信作答。
        这个包装保留原有的内部调用点与签名。
        """
        return dependency_ids_for_resource(item)

    @staticmethod
    def _dependency_status(
        dependency_ids: list[str], system_dependencies: list[Mapping[str, Any]]
    ) -> str:
        if not dependency_ids:
            return "not_required"
        if len(system_dependencies) != len(dependency_ids):
            return "unknown"
        statuses = [str(item.get("status") or "unknown") for item in system_dependencies]
        if all(item.get("ready") is True for item in system_dependencies):
            return "ready"
        for status in ("failed", "missing", "cancelled", "unknown"):
            if status in statuses:
                return status
        return statuses[0] if len(set(statuses)) == 1 else "unknown"

    @staticmethod
    def _public_builtin(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in item.items() if key != "content"}

    @staticmethod
    def _skill_record(item: Mapping[str, Any]) -> dict[str, Any]:
        source_key = str(item["source_key"])
        return {
            "catalog_id": f"skill:{source_key}",
            "type": "skill",
            "name": item.get("name", source_key.rsplit("/", 1)[-1]),
            "description": item.get("description", ""),
            "source_key": source_key,
            "owner": item.get("owner"),
            "repository": item.get("repository"),
            "branch": item.get("branch"),
            "directory": item.get("directory"),
            "source_url": item.get("source_url"),
            "installs": item.get("installs", 0),
        }

    @staticmethod
    def _matches(item: Mapping[str, Any], query: str) -> bool:
        if not query:
            return True
        haystack = " ".join(str(item.get(key, "")) for key in ("catalog_id", "name", "description", "source_key", "tags"))
        return query.casefold() in haystack.casefold()
