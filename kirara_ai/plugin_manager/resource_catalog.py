"""Unified resource catalog for discover-and-install flows.

The catalog is deliberately small and server-owned.  Search results are
metadata only; installation always resolves a server-generated catalog ID and
creates the same verified resource package used by offline imports.
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
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
    # ---- 随包角色提示词（不出网即可安装）---------------------------------
    #
    # 这些是 Claude Code 的 `agents/*.md`。本项目的「Agent」是
    # `AgentDefinition`（模型链 + 资源绑定），而那些文件是**行为说明**——
    # 在本项目的关系模型里对应 `prompt` 类资源：可绑到任意 Agent、进 system
    # 消息。不把它们硬塞成 Agent，因为一个 Agent 还要模型链与渠道绑定，
    # 而这些文件里一个都没有。
    {
        "catalog_id": "prompt:code-reviewer",
        "type": "prompt",
        "name": "code-reviewer",
        "description": "Senior code reviewer that evaluates changes across five dimensions — correctness, readability, architecture, security, and performance. Use for thorough code review before merge.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "PROMPT.md",
        "source": "bundled://kirara/prompt/code-reviewer",
        "bundled_dir": "prompts/code-reviewer",
        "tags": ["bundled", "prompt", "role"],
    },
    {
        "catalog_id": "prompt:content-creator",
        "type": "prompt",
        "name": "Content Creator",
        "description": "The Content Creator specializes in cross-platform content generation, from long-form blog posts to engaging video scripts and social media content. This agent understands how to adapt messaging across different formats w",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "PROMPT.md",
        "source": "bundled://kirara/prompt/content-creator",
        "bundled_dir": "prompts/content-creator",
        "tags": ["bundled", "prompt", "role"],
    },
    {
        "catalog_id": "prompt:security-auditor",
        "type": "prompt",
        "name": "security-auditor",
        "description": "Security engineer focused on vulnerability detection, threat modeling, and secure coding practices. Use for security-focused code review, threat analysis, or hardening recommendations.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "PROMPT.md",
        "source": "bundled://kirara/prompt/security-auditor",
        "bundled_dir": "prompts/security-auditor",
        "tags": ["bundled", "prompt", "role"],
    },
    {
        "catalog_id": "prompt:test-engineer",
        "type": "prompt",
        "name": "test-engineer",
        "description": "QA engineer specialized in test strategy, test writing, and coverage analysis. Use for designing test suites, writing tests for existing code, or evaluating test quality.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "PROMPT.md",
        "source": "bundled://kirara/prompt/test-engineer",
        "bundled_dir": "prompts/test-engineer",
        "tags": ["bundled", "prompt", "role"],
    },
    {
        "catalog_id": "prompt:web-performance-auditor",
        "type": "prompt",
        "name": "web-performance-auditor",
        "description": "Web performance engineer focused on Core Web Vitals, loading, rendering, and network optimization. Use for performance-focused audits, CWV analysis, and identifying structural performance anti-patterns in web application",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "PROMPT.md",
        "source": "bundled://kirara/prompt/web-performance-auditor",
        "bundled_dir": "prompts/web-performance-auditor",
        "tags": ["bundled", "prompt", "role"],
    },
    # ---- 本机在用、补齐为模板的 MCP -------------------------------------
    #
    # 只收「靠 npx 拉起、不依赖本机绝对路径」的。`node_repl` 与
    # `context-mode` 的命令是本机专属的绝对路径（Codex 自带运行时、
    # 全局 npm 目录），预置进去在任何别的机器上都是一条死配置——
    # 而界面上它看起来与其他模板一样正常。
    {
        "catalog_id": "mcp:puppeteer",
        "type": "mcp",
        "name": "Puppeteer Browser",
        "description": "用 Puppeteer 驱动 Chromium：导航、截图、执行页面脚本。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/puppeteer",
        "tags": ["browser", "automation", "stdio"],
        "runtime_dependency": "npx",
        "content": {
            "id": "puppeteer",
            "name": "Puppeteer Browser",
            "server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
                # env 一律留空：模板会写进 `data/resources/` 并可能随备份导出，
                # 预填令牌会跟着走。需要密钥的在 description 里说明要填什么。
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
            "description": "用 Puppeteer 驱动 Chromium：导航、截图、执行页面脚本。",
            "tags": ["browser", "automation", "stdio"],
            "homepage": "https://github.com/modelcontextprotocol/servers",
            "metadata": {"catalog_id": "mcp:puppeteer", "managed": True},
        },
    },
    {
        "catalog_id": "mcp:everything",
        "type": "mcp",
        "name": "MCP Everything (Reference)",
        "description": "MCP 官方参考实现：提供 echo、采样、资源等示例能力，用于验证 MCP 链路是否通。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/everything",
        "tags": ["reference", "diagnostics", "stdio"],
        "runtime_dependency": "npx",
        "content": {
            "id": "everything",
            "name": "MCP Everything (Reference)",
            "server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-everything"],
                # env 一律留空：模板会写进 `data/resources/` 并可能随备份导出，
                # 预填令牌会跟着走。需要密钥的在 description 里说明要填什么。
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
            "description": "MCP 官方参考实现：提供 echo、采样、资源等示例能力，用于验证 MCP 链路是否通。",
            "tags": ["reference", "diagnostics", "stdio"],
            "homepage": "https://github.com/modelcontextprotocol/servers",
            "metadata": {"catalog_id": "mcp:everything", "managed": True},
        },
    },
    {
        "catalog_id": "mcp:ui5",
        "type": "mcp",
        "name": "UI5 Development",
        "description": "SAP UI5 开发辅助：项目脚手架、API 参考与 lint。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/ui5",
        "tags": ["ui5", "sap", "development", "stdio"],
        "runtime_dependency": "npx",
        "content": {
            "id": "ui5",
            "name": "UI5 Development",
            "server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@ui5/mcp-server"],
                # env 一律留空：模板会写进 `data/resources/` 并可能随备份导出，
                # 预填令牌会跟着走。需要密钥的在 description 里说明要填什么。
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
            "description": "SAP UI5 开发辅助：项目脚手架、API 参考与 lint。",
            "tags": ["ui5", "sap", "development", "stdio"],
            "homepage": "https://github.com/SAP/ui5-mcp-server",
            "metadata": {"catalog_id": "mcp:ui5", "managed": True},
        },
    },
    {
        "catalog_id": "mcp:notion",
        "type": "mcp",
        "name": "Notion Workspace",
        "description": "读写 Notion 页面与数据库。需要在启用后自行填入 Notion 集成令牌。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/notion",
        "tags": ["notion", "documents", "stdio"],
        "runtime_dependency": "npx",
        "content": {
            "id": "notion",
            "name": "Notion Workspace",
            "server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@notionhq/notion-mcp-server"],
                # env 一律留空：模板会写进 `data/resources/` 并可能随备份导出，
                # 预填令牌会跟着走。需要密钥的在 description 里说明要填什么。
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
            "description": "读写 Notion 页面与数据库。需要在启用后自行填入 Notion 集成令牌。",
            "tags": ["notion", "documents", "stdio"],
            "homepage": "https://github.com/makenotion/notion-mcp-server",
            "metadata": {"catalog_id": "mcp:notion", "managed": True},
        },
    },
    # ---- 随包技能（不出网即可安装）-----------------------------------------
    #
    # 这批技能的正文随 wheel 与镜像一起分发，因此 `ensure_builtins()` 在离线
    # 部署里同样能把它们装上——与上面 `skill:agent-browser` 那条从 GitHub 下载
    # 的条目形成对照：后者装不上只是少一条可选技能，而随包技能装不上意味着
    # 「开箱就该有的东西没有」。
    #
    # 只收**纯文档**技能。带 `.sh` / `.js` / `.py` 脚本或二进制样例的技能装进
    # 运行时镜像会得到一个「启用了但跑不起来」的东西——镜像里没有 Node，
    # 也没有那些脚本要的解释器与依赖，而界面上它看起来是好的。
    {
        "catalog_id": "skill:investigate-first",
        "type": "skill",
        "name": "investigate-first",
        "description": "Diagnose ambiguous failures before editing. Use for unknown causes, intermittent behavior, performance regressions, or investigations needing evidence-ranked hypotheses.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/investigate-first",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/investigate-first",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:debugging-and-error-recovery",
        "type": "skill",
        "name": "debugging-and-error-recovery",
        "description": "Guides systematic root-cause debugging. Use when tests fail, builds break, behavior doesn't match expectations, or you encounter any unexpected error. Use when you need a systematic approach to finding and fixing the roo",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/debugging-and-error-recovery",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/debugging-and-error-recovery",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:code-review-and-quality",
        "type": "skill",
        "name": "code-review-and-quality",
        "description": "Conducts multi-axis code review. Use before merging any change. Use when reviewing code written by yourself, another agent, or a human. Use when you need to assess code quality across multiple dimensions before it enters",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/code-review-and-quality",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/code-review-and-quality",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:code-simplification",
        "type": "skill",
        "name": "code-simplification",
        "description": "Simplifies code for clarity. Use when refactoring code for clarity without changing behavior. Use when code works but is harder to read, maintain, or extend than it should be. Use when reviewing code that has accumulated",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/code-simplification",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/code-simplification",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:test-driven-development",
        "type": "skill",
        "name": "test-driven-development",
        "description": "Drives development with tests. Use when implementing any logic, fixing any bug, or changing any behavior. Use when you need to prove that code works, when a bug report arrives, or when you're about to modify existing fun",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/test-driven-development",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/test-driven-development",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:safe-refactor",
        "type": "skill",
        "name": "safe-refactor",
        "description": "Restructure code while preserving behavior. Use for extraction, consolidation, ownership moves, or cleanup where verification must bracket structural edits.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/safe-refactor",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/safe-refactor",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:api-and-interface-design",
        "type": "skill",
        "name": "api-and-interface-design",
        "description": "Guides stable API and interface design. Use when designing APIs, module boundaries, or any public interface. Use when creating REST or GraphQL endpoints, defining type contracts between modules, or establishing boundarie",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/api-and-interface-design",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/api-and-interface-design",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:documentation-and-adrs",
        "type": "skill",
        "name": "documentation-and-adrs",
        "description": "Records decisions and documentation. Use when making architectural decisions, changing public APIs, shipping features, or when you need to record context that future engineers and agents will need to understand the codeb",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/documentation-and-adrs",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/documentation-and-adrs",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:planning-and-task-breakdown",
        "type": "skill",
        "name": "planning-and-task-breakdown",
        "description": "Breaks work into ordered tasks. Use when you have a spec or clear requirements and need to break work into implementable tasks. Use when a task feels too large to start, when you need to estimate scope, or when parallel ",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/planning-and-task-breakdown",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/planning-and-task-breakdown",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:spec-driven-development",
        "type": "skill",
        "name": "spec-driven-development",
        "description": "Creates specs before coding. Use when starting a new project, feature, or significant change and no specification exists yet. Use when requirements are unclear, ambiguous, or only exist as a vague idea.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/spec-driven-development",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/spec-driven-development",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:source-driven-development",
        "type": "skill",
        "name": "source-driven-development",
        "description": "Grounds every implementation decision in official documentation. Use when you want authoritative, source-cited code free from outdated patterns. Use when building with any framework or library where correctness matters.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/source-driven-development",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/source-driven-development",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:incremental-implementation",
        "type": "skill",
        "name": "incremental-implementation",
        "description": "Delivers changes incrementally. Use when implementing any feature or change that touches more than one file. Use when you're about to write a large amount of code at once, or when a task feels too big to land in one step",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/incremental-implementation",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/incremental-implementation",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:verification-before-completion",
        "type": "skill",
        "name": "verification-before-completion",
        "description": "Use when about to claim work is complete, fixed, or passing, before committing or creating PRs - requires running verification commands and confirming output before making any success claims; evidence before assertions a",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/verification-before-completion",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/verification-before-completion",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:doubt-driven-development",
        "type": "skill",
        "name": "doubt-driven-development",
        "description": "Subjects every non-trivial decision to a fresh-context adversarial review before it stands. Use when correctness matters more than speed, when working in unfamiliar code, when stakes are high (production, security-sensit",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/doubt-driven-development",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/doubt-driven-development",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:security-and-hardening",
        "type": "skill",
        "name": "security-and-hardening",
        "description": "Hardens code against vulnerabilities. Use when handling user input, authentication, data storage, or external integrations. Use when building any feature that accepts untrusted data, manages user sessions, or interacts w",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/security-and-hardening",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/security-and-hardening",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:performance-optimization",
        "type": "skill",
        "name": "performance-optimization",
        "description": "Optimizes application performance across frontend, backend, queries, and databases. Use when performance requirements exist, when you suspect performance regressions, when Core Web Vitals or load times need improvement, ",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/performance-optimization",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/performance-optimization",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:observability-and-instrumentation",
        "type": "skill",
        "name": "observability-and-instrumentation",
        "description": "Instruments code so production behavior is visible and diagnosable. Use when adding logging, metrics, tracing, or alerting. Use when shipping any feature that runs in production and you need evidence it works. Use when p",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/observability-and-instrumentation",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/observability-and-instrumentation",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:git-workflow-and-versioning",
        "type": "skill",
        "name": "git-workflow-and-versioning",
        "description": "Structures git workflow practices. Use when making any code change. Use when committing, branching, resolving conflicts, or when you need to organize work across multiple parallel streams. Use when cutting a release, cho",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/git-workflow-and-versioning",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/git-workflow-and-versioning",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:ci-cd-and-automation",
        "type": "skill",
        "name": "ci-cd-and-automation",
        "description": "Automates CI/CD pipeline setup. Use when setting up or modifying build and deployment pipelines. Use when you need to automate quality gates, configure test runners in CI, or establish deployment strategies.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/ci-cd-and-automation",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/ci-cd-and-automation",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:deprecation-and-migration",
        "type": "skill",
        "name": "deprecation-and-migration",
        "description": "Manages deprecation and migration. Use when removing old systems, APIs, or features. Use when migrating users from one implementation to another. Use when deciding whether to maintain or sunset existing code.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/deprecation-and-migration",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/deprecation-and-migration",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:context-engineering",
        "type": "skill",
        "name": "context-engineering",
        "description": "Optimizes agent context setup. Use when starting a new session, when agent output quality degrades, when switching between tasks, or when you need to configure rules files and context for a project.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/context-engineering",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/context-engineering",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:writing-plans",
        "type": "skill",
        "name": "writing-plans",
        "description": "Use when you have a spec or requirements for a multi-step task, before touching code",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/writing-plans",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/writing-plans",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:using-agent-skills",
        "type": "skill",
        "name": "using-agent-skills",
        "description": "Discovers and invokes agent skills. Use when starting a session or when you need to discover which skill applies to the current task. This is the meta-skill that governs how all other skills are discovered and invoked.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/using-agent-skills",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/using-agent-skills",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:interview-me",
        "type": "skill",
        "name": "interview-me",
        "description": "Extracts what the user actually wants instead of what they think they should want. Achieves this through one-question-at-a-time interview until ~95% confidence about the underlying intent. Use when an ask is underspecifi",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/interview-me",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/interview-me",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:color-expert",
        "type": "skill",
        "name": "color-expert",
        "description": "Use when working with color naming, color theory, color spaces, color definitions, or any task involving color knowledge - palettes, ramps, gradients, conversions, accessibility, perceptual matching, pigment mixing, prin",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/color-expert",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/color-expert",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:frontend-design",
        "type": "skill",
        "name": "frontend-design",
        "description": "Guidance for distinctive, intentional visual design when building new UI or reshaping an existing one. Helps with aesthetic direction, typography, and making choices that don't read as templated defaults.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/frontend-design",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/frontend-design",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:frontend-ui-engineering",
        "type": "skill",
        "name": "frontend-ui-engineering",
        "description": "Builds production-quality, accessible, responsive user-facing UIs. Use when building or modifying interfaces and pages, creating components, implementing layouts, meeting WCAG accessibility requirements, managing state, ",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/frontend-ui-engineering",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/frontend-ui-engineering",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:learn-codebase",
        "type": "skill",
        "name": "learn-codebase",
        "description": "Prime a codebase by reading every source file in full. Use when starting work on a new or unfamiliar project, or when the user asks to \"learn the codebase\", \"read the codebase\", \"prime\", or \"get up to speed\".",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/learn-codebase",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/learn-codebase",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:smart-explore",
        "type": "skill",
        "name": "smart-explore",
        "description": "Token-optimized structural code search using tree-sitter AST parsing. Use instead of reading full files when you need to understand code structure, find functions, or explore a codebase efficiently.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/smart-explore",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/smart-explore",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:make-plan",
        "type": "skill",
        "name": "make-plan",
        "description": "Create a detailed, phased implementation plan with documentation discovery. Use when asked to plan a feature, task, or multi-step implementation — especially before executing with do.",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/make-plan",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/make-plan",
        "tags": ["bundled", "skill"],
    },
    {
        "catalog_id": "skill:what-the",
        "type": "skill",
        "name": "what-the",
        "description": "\"What the? Use when the user wants a plain-English breakdown of something technical — the who, what, where, why, and when.\"",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "SKILL.md",
        "source": "bundled://kirara/skill/what-the",
        # 随包：正文在 wheel 里，安装不出网。见 `_install_bundled_skill`。
        "bundled_dir": "skills/what-the",
        "tags": ["bundled", "skill"],
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
        if remote_status["status"] == "ok":
            # 远端已经按 offset 返回了它的那一页，因此**本地条目必须自己应用
            # 同一个 offset**，否则两侧的页码语义不一致：远端给的是第 N 页，
            # 而本地给的永远是第 1 页，合并后本地条目把远端那页挤出 `[:limit]`。
            #
            # 此前这里是 `ordered[:limit]`，靠一个没写出来的前提成立：
            # 「本地内置里没有任何条目命中这个查询」。内置从 20 条扩到 52 条之后
            # 那个前提就不再成立了——`skill:frontend-ui-engineering` 的描述里有
            # 「interfaces and pages」，于是它命中 `q=page` 并占掉了唯一的名额。
            #
            # 远端结果排在前面：查询语义上「在线索引的这一页」是用户要的主体，
            # 本地命中是补充。
            local_hits = [
                item for item in ordered if not item.get("source_key", "").count("/")
            ]
            remote_hits = [item for item in ordered if item not in local_hits]
            page = (remote_hits + local_hits[offset : offset + limit])[:limit]
        else:
            page = ordered[offset : offset + limit]
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
            # `version` 只有**内置条目**才有。`_find()` 为在线搜索结果合成的
            # skill 条目没有这个键（版本号来自上游、本地无从比较），
            # 因此必须先判存在再比较——直接 `item["version"]` 会在
            # 「重装一个已装的远端技能」这条路径上抛 `KeyError: 'version'`。
            bundled_version = item.get("version")
            if bundled_version is not None and Version(str(bundled_version)) > Version(
                str(existing["current_version"])
            ):
                # 随包资源（技能与角色提示词）与其他内置一样按版本推进。
                if item.get("bundled_dir"):
                    return self._backfilled(
                        item, self._install_bundled_skill(item, update=True)
                    )
                if item["type"] != "skill":
                    return self._backfilled(item, self._install_builtin(item, update=True))
            return self._backfilled(item, existing)
        if item.get("bundled_dir"):
            # 随包资源：正文就在 wheel / 镜像里，安装不出网。判据是
            # **有没有 `bundled_dir`**，而不是 `type == "skill"`——随包角色提示词
            # 与随包技能的落盘方式完全一样（打包一个目录的全部文件），
            # 只有 type 不同。按 type 判会让 `prompt:*` 落到下面的
            # `_install_builtin`，那里读 `item["content"]` 而随包条目没有这个键，
            # 于是抛 `KeyError: 'content'`——一个与「这个资源装不上」毫无关系的错。
            #
            # 与 `skill:agent-browser` 那种从 GitHub 下载的条目分开处理：
            # 后者装不上只是少一条可选技能，而随包资源装不上意味着
            # 「开箱就该有的东西没有」，两者的失败含义不同。
            return self._backfilled(item, self._install_bundled_skill(item))
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
        return self._install_files(item, {str(item["entry"]): data}, update=update)

    def _install_files(
        self,
        item: Mapping[str, Any],
        files: Mapping[str, bytes],
        *,
        update: bool = False,
    ) -> dict[str, Any]:
        """Build the verified archive for one resource and hand it to lifecycle.

        抽出来是因为随包技能与单文件内置只差「files 怎么来」这一步：
        清单、`content_sha256`、zip 打包与落盘必须逐字节一致，
        否则两条路径装出来的资源在校验上不同形，而那种差异只会在
        下一次载入时才暴露。
        """

        # 必须**按路径排序**：校验端 `ResourceLifecycleService._content_hash()`
        # 算的是 `sorted(files, key=path)`，这里不排序时两侧对同一批文件得出
        # 不同的 `content_sha256`，安装直接被判为
        # 「resource content digest does not match manifest」。
        #
        # 单文件资源上两种顺序恰好一致，所以这个错在内置提示词、记忆、MCP
        # 上完全没有症状——只有多文件的随包技能才会暴露。
        records = [
            {"path": path, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            for path, value in sorted(files.items())
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

    #: 随包资源的根目录（`bundled/skills/<id>/`）。
    #:
    #: 用 `__file__` 定位而不是 `Path.cwd()`：安装后这些文件住在 site-packages
    #: 里，而进程的工作目录是部署者的，两者无关。
    _BUNDLED_ROOT = pathlib.Path(__file__).resolve().parent / "bundled"

    def _install_bundled_skill(
        self, item: Mapping[str, Any], *, update: bool = False
    ) -> dict[str, Any]:
        """Package a resource that ships inside the wheel, without any network call.

        名字沿用 `_skill` 是为了不改动既有调用点，但它对**任何**带
        `bundled_dir` 的类型都成立（目前是 skill 与 prompt）。

        与 `_install_builtin` 的区别只有一处：正文来自**一个目录的全部文件**
        而不是单个 `content` 字段。技能天然是多文件的（`SKILL.md` 加若干
        参考文档），把它们拼成一个字段会丢掉目录结构，而 `SKILL.md` 里的
        相对链接正是按那个结构写的。
        """

        directory = self._BUNDLED_ROOT / str(item["bundled_dir"])
        if not directory.is_dir():
            raise ResourceCatalogError(
                f"随包技能目录缺失：{directory}（wheel 打包漏了 package-data？）"
            )
        files: dict[str, bytes] = {}
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(directory).as_posix()
            files[relative] = path.read_bytes()
        if str(item["entry"]) not in files:
            raise ResourceCatalogError(
                f"随包技能 {item['catalog_id']} 缺少入口文件 {item['entry']}"
            )
        return self._install_files(item, files, update=update)

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
