from __future__ import annotations

import hashlib
import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentRegistry,
    AgentRuntimeExecutor,
    AgentHookRuntime,
    ChannelContext,
    ResourceBinding,
    RuntimeStatus,
)
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.tool import Function, ToolCall
from kirara_ai.llm.resilience import ChatExecutionResult, FailoverExecutionError
from kirara_ai.memory.entry import MemoryEntry
from kirara_ai.memory.memory_manager import MemoryManager
from kirara_ai.memory.persistences.file_persistence import FileMemoryPersistence
from kirara_ai.memory.scopes import MemberScope
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService, _BUILTINS
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.media.carrier.registry import MediaCarrierRegistry
from kirara_ai.media.carrier.service import MediaCarrierService
from kirara_ai.media.manager import MediaManager
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.agent_runtime.session_store import SessionStore
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context


CREATOR = RuntimePrincipal(subject="persistent-runtime-test-creator", is_creator=True)


@pytest.fixture
def creator_principal():
    with runtime_principal_context(CREATOR):
        yield


class ControlledLLM:
    def __init__(self, responses):
        self.responses = {key: list(value) for key, value in responses.items()}
        self.requests = []

    def execute_chat(self, request, **_options):
        self.requests.append(request)
        response = self.responses[request.model].pop(0)
        if isinstance(response, BaseException):
            raise response
        return ChatExecutionResult(
            response=response,
            trace_id=f"persistent-trace-{len(self.requests)}",
            attempts=[],
        )


class Context7Transport:
    def __init__(self):
        self.calls = []
        self.tools = {
            "resolve-library-id": SimpleNamespace(
                server_id="context7",
                original_name="resolve-library-id",
                tool_info=SimpleNamespace(
                    name="resolve-library-id",
                    description="Resolve a library",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "libraryName": {"type": "string"},
                            "query": {"type": "string"},
                        },
                        "required": ["libraryName", "query"],
                    },
                ),
            )
        }

    def get_tools(self):
        return self.tools

    async def call_tool(self, name, args, **options):
        self.calls.append((name, args, options))
        return SimpleNamespace(
            content=[SimpleNamespace(text="/pytest-dev/pytest documentation")],
            isError=False,
        )


def _archive(resource_id: str, resource_type: str, entry: str, content, source: str):
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    file_record = {
        "path": entry,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    content_sha256 = hashlib.sha256(
        f"{entry}:{len(data)}:{file_record['sha256']}\n".encode("ascii")
    ).hexdigest()
    manifest = {
        "resource_id": resource_id,
        "type": resource_type,
        "version": "1.0.0",
        "source": source,
        "entry": entry,
        "permissions": ["workflow.read"],
        "files": [file_record],
        "content_sha256": content_sha256,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr(entry, data)
    return output.getvalue()


def _install(lifecycle, resource_id, resource_type, entry, content, source):
    archive = lifecycle.imports_path / f"{resource_id}.zip"
    archive.write_bytes(_archive(resource_id, resource_type, entry, content, source))
    try:
        return lifecycle.install_archive(archive)
    finally:
        archive.unlink(missing_ok=True)


def _install_downloaded_skill(lifecycle, downloaded_root: Path) -> dict:
    """Install the exact manifest and files produced by the remote Skill download."""

    manifest_path = downloaded_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_path = lifecycle.imports_path / f"{manifest['resource_id']}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for file_record in manifest["files"]:
            archive.writestr(
                file_record["path"],
                (downloaded_root / file_record["path"]).read_bytes(),
            )
    try:
        return lifecycle.install_archive(archive_path)
    finally:
        archive_path.unlink(missing_ok=True)


def _context(channel="telegram"):
    return ChannelContext(
        channel_type=channel,
        adapter_instance=f"{channel}-main",
        account_scope="main",
        conversation_scope="c2c:researcher",
        sender_scope="researcher",
    )


def _message(text="Resolve the current pytest documentation"):
    return IMMessage(
        ChatSender.from_c2c_chat("researcher", "Researcher"),
        [TextMessage(text)],
    )


def _call(name="resolve-library-id"):
    return ToolCall(
        id="context7-call",
        type="function",
        function=Function(
            name=name,
            arguments={
                "libraryName": "pytest",
                "query": "fixtures and parametrization",
            },
        ),
    )


def _memory_manager(data_path: Path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    container.register(GlobalConfig, config)
    container.register(MediaCarrierRegistry, MediaCarrierRegistry(container))
    media = MediaManager(media_dir=data_path / "media")
    container.register(MediaManager, media)
    carrier = MediaCarrierService(container, media)
    container.register(MediaCarrierService, carrier)
    manager = MemoryManager(
        container,
        persistence=FileMemoryPersistence(str(data_path / "memory")),
    )
    manager.register_scope("member", MemberScope)
    return manager


def _ensure_builtins_after_legacy_prompt(lifecycle, catalog):
    legacy_prompt = deepcopy(
        next(item for item in _BUILTINS if item["catalog_id"] == "prompt:office-research")
    )
    legacy_prompt["version"] = "1.0.0"
    legacy_prompt["content"] = "Legacy office prompt.\n"
    catalog._install_builtin(legacy_prompt)
    catalog.ensure_builtins()


def _agent(lifecycle):
    def binding(resource_id, resource_type):
        current_version = lifecycle.get_resource(resource_id)["current_version"]
        return lifecycle.resolve_binding(
            resource_id,
            resource_type,
            version=current_version,
            enabled=True,
        )

    return AgentDefinition(
        agent_id="persistent-unified-agent",
        model_priority=("qa-primary", "qa-fallback"),
        capabilities=frozenset({"research", "process.execute"}),
        prompt_bindings=(binding("prompt.office-research", "prompt"),),
        skill_bindings=(binding("skill.agent-browser", "skill"),),
        memory_bindings=(binding("memory.research-context", "memory"),),
        mcp_bindings=(binding("mcp.context7", "mcp"),),
        hook_bindings=(binding("hook.ai-debug", "hook"),),
        mcp_allowlist=frozenset({"context7.resolve-library-id"}),
        max_tool_iterations=2,
    )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("creator_principal")
async def test_persisted_catalog_resources_drive_one_real_agent_turn(tmp_path: Path):
    lifecycle = ResourceLifecycleService(tmp_path / "vps-data")
    catalog = ResourceCatalogService(lifecycle)
    _ensure_builtins_after_legacy_prompt(lifecycle, catalog)
    for resource_id in (
        "prompt.office-research",
        "memory.research-context",
        "mcp.context7",
        "hook.ai-debug",
    ):
        lifecycle.enable(resource_id, confirmed=True)
    _install(
        lifecycle,
        "skill.agent-browser",
        "skill",
        "SKILL.md",
        "Use browser automation for web application verification.",
        "catalog://test/skill/agent-browser",
    )
    lifecycle.enable("skill.agent-browser", confirmed=True)
    for resource_id in (
        "prompt.office-research",
        "memory.research-context",
        "mcp.context7",
        "hook.ai-debug",
        "skill.agent-browser",
    ):
        lifecycle.enable(resource_id, confirmed=True)

    agent = _agent(lifecycle)
    first_registry = AgentRegistry(tmp_path / "vps-data")
    first_registry.register(agent)
    first_registry.configure(
        agent,
        channels=("webui", "onebot", "qqbot", "telegram", "wecom"),
        accounts=(("telegram", "telegram-main", "main"),),
        sessions=(_context().session_key,),
    )
    reloaded_registry = AgentRegistry(tmp_path / "vps-data")
    assert reloaded_registry.resolve(_context()).agent_id == agent.agent_id
    assert reloaded_registry.resolve(_context("webui")).agent_id == agent.agent_id

    memory = _memory_manager(tmp_path / "vps-data")
    scope = memory.scope_registry.get_scope("member")
    memory.store(
        scope,
        MemoryEntry(
            sender=ChatSender.from_c2c_chat("researcher", "Researcher"),
            content="The team prefers cited documentation.",
            metadata={},
        ),
        extra_identifier="preexisting-memory",
    )
    # The runtime-specific memory key is intentionally populated through the
    # same public persistence API used by production MemoryManager.
    from kirara_ai.agent_runtime.executor import AgentRuntimeExecutor
    memory_key = AgentRuntimeExecutor._memory_key(_context(), agent.agent_id)
    memory.store(
        scope,
        MemoryEntry(
            sender=ChatSender.from_c2c_chat("researcher", "Researcher"),
            content="Persisted research context.",
            metadata={
                "agent_runtime": {
                    "version": 1,
                    "agent_id": agent.agent_id,
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Persisted research context."}],
                    },
                }
            },
        ),
        extra_identifier=memory_key,
    )

    audit = []
    hook = AgentHookRuntime(
        resource_service=ResourceLifecycleService(tmp_path / "vps-data"),
        audit_sink=audit.append,
    )
    llm = ControlledLLM(
        {
            "qa-primary": [FailoverExecutionError("primary unavailable", attempts=[])],
            "qa-fallback": [
                LLMChatResponse(
                    model="qa-fallback",
                    message=Message(role="assistant", content=[], tool_calls=[_call()]),
                ),
                LLMChatResponse(
                    model="qa-fallback",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="pytest documentation resolved")],
                    ),
                ),
            ],
        }
    )
    mcp_container = DependencyContainer()
    mcp_container.register(GlobalConfig, GlobalConfig())
    mcp_container.register(ResourceLifecycleService, lifecycle)
    mcp = MCPServerManager(mcp_container)

    try:
        mcp.load_servers()
        assert set(mcp.get_all_servers()) == {"context7"}
        assert (
            mcp.get_server("context7").server_config.metadata["resource_id"]
            == "mcp.context7"
        )
        assert await mcp.connect_server("context7") is True
        assert {"resolve-library-id", "query-docs"}.issubset(mcp.get_tools())

        executor = AgentRuntimeExecutor(
            agent_registry=reloaded_registry,
            llm_manager=llm,
            mcp_manager=mcp,
            resource_service=ResourceLifecycleService(tmp_path / "vps-data"),
            session_store=SessionStore(tmp_path / "vps-data"),
            memory_manager=memory,
            hook_runtime=hook,
            audit_sink=audit.append,
        )

        result = await executor.run(
            _context(),
            _message(),
            session_mcp_allowlist={"context7.resolve-library-id"},
            workflow_mcp_allowlist={"context7.resolve-library-id"},
        )

        assert result.status is RuntimeStatus.COMPLETED
        assert result.text == "pytest documentation resolved"
        assert [request.model for request in llm.requests] == [
            "qa-primary",
            "qa-fallback",
            "qa-fallback",
        ]
        system = llm.requests[1].messages[0].content[0].text
        assert "我是上班族" in system
        assert "browser automation" in system
        assert "研究型 Agent" in system
        assert "resolve-library-id" in {tool.name for tool in llm.requests[1].tools}
        tool_messages = [
            message for message in llm.requests[2].messages if message.role == "tool"
        ]
        assert tool_messages
        assert "pytest" in str(tool_messages[-1].content).lower()
        assert any(
            record.get("server") == "context7"
            and record.get("operation") == "call_tool"
            and record.get("outcome") == "success"
            for record in mcp.audit_records
        )
        assert {item["event"] for item in audit if item.get("outcome") == "success"} >= {
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "Stop",
        }
        assert result.snapshot.resources[0].content_sha256 == lifecycle.get_resource(
            "prompt.office-research"
        )["content_sha256"]
        assert list((tmp_path / "vps-data" / "sessions").glob("*.json"))
        assert list((tmp_path / "vps-data" / "memory").glob("*.json"))

        restarted_memory = _memory_manager(tmp_path / "vps-data")
        restored = restarted_memory.query(
            restarted_memory.scope_registry.get_scope("member"),
            ChatSender.from_c2c_chat("researcher", "Researcher"),
            extra_identifier=memory_key,
        )
        assert any("pytest documentation resolved" in entry.content for entry in restored)
    finally:
        await mcp.stop_server("context7")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_persisted_hook_can_block_context7_before_transport_call(tmp_path: Path):
    lifecycle = ResourceLifecycleService(tmp_path / "vps-data")
    catalog = ResourceCatalogService(lifecycle)
    _ensure_builtins_after_legacy_prompt(lifecycle, catalog)
    for resource_id in (
        "prompt.office-research",
        "memory.research-context",
        "mcp.context7",
        "hook.ai-debug",
    ):
        lifecycle.enable(resource_id, confirmed=True)
    _install(
        lifecycle,
        "skill.agent-browser",
        "skill",
        "SKILL.md",
        "Use browser automation for web application verification.",
        "catalog://test/skill/agent-browser",
    )
    lifecycle.enable("skill.agent-browser", confirmed=True)
    blocked_hook = json.dumps(
        {"events": {"PreToolUse": {"handler": "deny.tool", "deny": True}}}
    )
    _install(
        lifecycle,
        "hook.block-context7",
        "hook",
        "hook.json",
        blocked_hook,
        "catalog://test/hook/block-context7",
    )
    lifecycle.enable("hook.block-context7", confirmed=True)
    base = _agent(lifecycle)
    base = AgentDefinition(
        **{
            **base.__dict__,
            "hook_bindings": (
                lifecycle.resolve_binding(
                    "hook.block-context7", "hook", version="1.0.0", enabled=True
                ),
            ),
        }
    )
    registry = AgentRegistry()
    registry.register(base)
    registry.set_default(base.agent_id)
    mcp = Context7Transport()
    llm = ControlledLLM(
        {
            "qa-primary": [
                LLMChatResponse(
                    model="qa-primary",
                    message=Message(role="assistant", content=[], tool_calls=[_call()]),
                ),
                LLMChatResponse(
                    model="qa-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="tool was denied")],
                    ),
                ),
            ]
        }
    )
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=mcp,
        resource_service=ResourceLifecycleService(tmp_path / "vps-data"),
        hook_runtime=AgentHookRuntime(
            resource_service=ResourceLifecycleService(tmp_path / "vps-data"),
            handlers={"deny.tool": lambda _payload: {"deny": True}},
        ),
    )
    result = await executor.run(_context("webui"), _message())

    assert result.status is RuntimeStatus.COMPLETED
    assert result.text == "tool was denied"
    assert not mcp.calls


#: 一次真实下载留下的 Skill 产物根目录。
#:
#: 这份产物**刻意不进仓库**（`.gitignore` 的 `/.qa-*/`）：它是从真实来源下载的
#: 第三方内容，体积与许可都不该由本仓库承担。因此下面两条用例依赖一个
#: 「可能不存在」的路径，而它们**必须**在缺失时跳过而不是失败——
#: 硬断言它存在等于要求每一台 CI runner 和每一个 clone 都先手工下载一遍，
#: 而这两条用例真正要证明的事（真实下载的 Skill 能进运行时、正文能到达模型）
#: 在没有那份下载物时根本无从证明。跳过是诚实的，失败是误报。
#:
#: 本机有产物时照常运行，因此这个证据链并没有消失，只是不再要求它无条件在场。
_REAL_SKILL_ROOT = (
    Path(__file__).resolve().parents[2]
    / ".qa-real-agent-browser-20260827"
    / "resources"
    / "installed"
    / "skill.c914025169da0bca"
    / "1.0.0"
)


def _require_real_downloaded_skill() -> Path:
    """返回真实下载的 Skill 目录；不存在时跳过本用例。

    两个文件都要在：`manifest.json` 给出来源与内容摘要，`SKILL.md` 是正文。
    只有一个在说明那次下载没写完，那种半份产物同样不能用来断言任何事。
    """
    missing = [
        name
        for name in ("manifest.json", "SKILL.md")
        if not (_REAL_SKILL_ROOT / name).is_file()
    ]
    if missing:
        pytest.skip(
            "缺少真实下载的 Skill 产物"
            f"（{_REAL_SKILL_ROOT} 下没有 {', '.join(missing)}）。"
            "该目录按 .gitignore 的 /.qa-*/ 刻意不随仓库分发；"
            "需要这条证据时先在本机走一次真实下载。"
        )
    return _REAL_SKILL_ROOT


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("creator_principal")
async def test_real_downloaded_agent_browser_skill_enters_persistent_runtime(tmp_path: Path):
    """Prove the server-downloaded Skill, rather than a test stub, reaches the model."""

    downloaded_root = _require_real_downloaded_skill()

    downloaded_manifest = json.loads(
        (downloaded_root / "manifest.json").read_text(encoding="utf-8")
    )
    downloaded_skill = (downloaded_root / "SKILL.md").read_text(encoding="utf-8")
    assert downloaded_manifest["source_key"] == "vercel-labs/agent-browser:skills/agent-browser"
    assert downloaded_manifest["content_sha256"] == hashlib.sha256(
        "SKILL.md:3457:3a7520e64de2dfaa516b6721c001e84e06e3c06a187b82af029ae721540ebfac\n".encode(
            "ascii"
        )
    ).hexdigest()
    assert "name: agent-browser" in downloaded_skill
    assert "agent-browser skills get core" in downloaded_skill

    lifecycle = ResourceLifecycleService(tmp_path / "vps-data")
    catalog = ResourceCatalogService(lifecycle)
    _ensure_builtins_after_legacy_prompt(lifecycle, catalog)
    for resource_id in (
        "prompt.office-research",
        "memory.research-context",
        "mcp.context7",
        "hook.ai-debug",
    ):
        lifecycle.enable(resource_id, confirmed=True)
    installed = _install_downloaded_skill(lifecycle, downloaded_root)
    lifecycle.enable(installed["resource_id"], confirmed=True)

    def binding(resource_id, resource_type):
        current_version = lifecycle.get_resource(resource_id)["current_version"]
        return lifecycle.resolve_binding(
            resource_id,
            resource_type,
            version=current_version,
            enabled=True,
        )

    agent = AgentDefinition(
        agent_id="persistent-real-agent-browser-agent",
        model_priority=("qa-primary", "qa-fallback"),
        capabilities=frozenset({"research", "process.execute"}),
        prompt_bindings=(binding("prompt.office-research", "prompt"),),
        skill_bindings=(binding(installed["resource_id"], "skill"),),
        memory_bindings=(binding("memory.research-context", "memory"),),
        mcp_bindings=(binding("mcp.context7", "mcp"),),
        hook_bindings=(binding("hook.ai-debug", "hook"),),
        mcp_allowlist=frozenset({"context7.resolve-library-id"}),
        max_tool_iterations=2,
    )
    registry = AgentRegistry(tmp_path / "vps-data")
    registry.register(agent)
    registry.set_default(agent.agent_id)

    audit = []
    memory = _memory_manager(tmp_path / "vps-data")
    def record_audit(event):
        audit.append(event)
        lifecycle.append_runtime_audit(event)

    hook = AgentHookRuntime(
        resource_service=ResourceLifecycleService(tmp_path / "vps-data"),
        audit_sink=record_audit,
    )
    llm = ControlledLLM(
        {
            "qa-primary": [FailoverExecutionError("primary unavailable", attempts=[])],
            "qa-fallback": [
                LLMChatResponse(
                    model="qa-fallback",
                    message=Message(role="assistant", content=[], tool_calls=[_call()]),
                ),
                LLMChatResponse(
                    model="qa-fallback",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="real downloaded skill used")],
                    ),
                ),
            ],
        }
    )
    mcp_container = DependencyContainer()
    mcp_container.register(GlobalConfig, GlobalConfig())
    mcp_container.register(ResourceLifecycleService, lifecycle)
    mcp = MCPServerManager(mcp_container, audit_sink=record_audit)

    try:
        mcp.load_servers()
        assert await mcp.connect_server("context7") is True
        executor = AgentRuntimeExecutor(
            agent_registry=registry,
            llm_manager=llm,
            mcp_manager=mcp,
            resource_service=ResourceLifecycleService(tmp_path / "vps-data"),
            session_store=SessionStore(tmp_path / "vps-data"),
            memory_manager=memory,
            hook_runtime=hook,
            audit_sink=record_audit,
        )
        result = await executor.run(
            _context(),
            _message(),
            session_mcp_allowlist={"context7.resolve-library-id"},
            workflow_mcp_allowlist={"context7.resolve-library-id"},
        )

        assert result.status is RuntimeStatus.COMPLETED
        assert result.text == "real downloaded skill used"
        assert [request.model for request in llm.requests] == [
            "qa-primary",
            "qa-fallback",
            "qa-fallback",
        ]
        system = llm.requests[1].messages[0].content[0].text
        # 渐进披露（需求 10，与主流 Agent 客户端同一原理）：真实下载的这份
        # SKILL.md 带前置元数据，因此系统提示词里只有一行目录，正文由
        # `skill_<id>` 工具在模型真的要用时取回——而不是每一轮都整篇塞进上下文。
        #
        # 这里断言的是「模型真的拿得到这份技能」这件事没变，变的只是拿法：
        # 目录里有它的名字与用途，工具列表里有对应的可调用工具。
        skill_tool_name = f"skill_{installed['resource_id']}"
        assert "agent-browser" in system
        assert skill_tool_name in system
        # 正文不在系统提示词里——这正是这次改动省下的那部分成本。
        assert "agent-browser skills get core" not in system
        assert "我是上班族" in system
        offered_tools = {tool.name for tool in llm.requests[1].tools}
        assert "resolve-library-id" in offered_tools
        assert skill_tool_name in offered_tools
        assert result.snapshot is not None
        skill_binding = next(
            item
            for item in result.snapshot.resources
            if item.resource_id == installed["resource_id"]
        )
        assert skill_binding.version == downloaded_manifest["version"]
        assert skill_binding.content_sha256 == downloaded_manifest["content_sha256"]
        assert skill_binding.source == downloaded_manifest["source"]
        assert result.correlation_id
        assert {
            item.get("correlation_id")
            for item in audit
            if item.get("correlation_id") is not None
        } == {result.correlation_id}
        assert any(
            item.get("component") == "mcp"
            and item.get("operation") == "call_tool"
            and item.get("correlation_id") == result.correlation_id
            for item in audit
        )
        persisted = lifecycle.list_audit(
            correlation_id=result.correlation_id,
            limit=200,
        )
        assert {item["component"] for item in persisted["items"]} >= {
            "agent_runtime",
            "agent_hook",
            "mcp",
        }
    finally:
        await mcp.stop_server("context7")


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_calling_the_real_skill_tool_returns_the_downloaded_body(tmp_path: Path):
    """模型调用 `skill_<id>` 时，拿回的必须是**真实下载的那份正文**。

    上一个用例证明了广告与工具都在，但那还不足以说明技能真的能被用上——
    渐进披露只有在「调用之后正文确实到达模型」时才成立。少了这一跳，
    我们就只是把整篇注入换成了一句谁也兑现不了的目录，
    而那比整篇注入更糟：界面上技能显示已启用，实际效果是零。

    这里用真实下载的 agent-browser SKILL.md（不是测试桩），
    并断言第二轮请求的 tool 消息里出现了正文中的具体命令。
    """

    downloaded_root = _require_real_downloaded_skill()
    downloaded_skill = (downloaded_root / "SKILL.md").read_text(encoding="utf-8")

    lifecycle = ResourceLifecycleService(tmp_path / "vps-data")
    catalog = ResourceCatalogService(lifecycle)
    _ensure_builtins_after_legacy_prompt(lifecycle, catalog)
    lifecycle.enable("prompt.office-research", confirmed=True)
    installed = _install_downloaded_skill(lifecycle, downloaded_root)
    lifecycle.enable(installed["resource_id"], confirmed=True)
    resource_id = installed["resource_id"]
    skill_tool_name = f"skill_{resource_id}"

    def binding(rid, resource_type):
        return lifecycle.resolve_binding(
            rid,
            resource_type,
            version=lifecycle.get_resource(rid)["current_version"],
            enabled=True,
        )

    agent = AgentDefinition(
        agent_id="skill-tool-agent",
        model_priority=("qa-fallback",),
        capabilities=frozenset({"research"}),
        prompt_bindings=(binding("prompt.office-research", "prompt"),),
        skill_bindings=(binding(resource_id, "skill"),),
        max_tool_iterations=2,
    )
    registry = AgentRegistry(tmp_path / "vps-data")
    registry.register(agent)
    registry.set_default(agent.agent_id)

    llm = ControlledLLM(
        {
            "qa-fallback": [
                LLMChatResponse(
                    model="qa-fallback",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[
                            ToolCall(
                                id="skill-call",
                                type="function",
                                function=Function(name=skill_tool_name, arguments={}),
                            )
                        ],
                    ),
                ),
                LLMChatResponse(
                    model="qa-fallback",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="browser automation planned")],
                    ),
                ),
            ]
        }
    )
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=_EmptyMCPManager(),
        resource_service=ResourceLifecycleService(tmp_path / "vps-data"),
    )

    result = await executor.run(_context(), _message("open a browser and read the page"))

    assert result.status is RuntimeStatus.COMPLETED
    assert result.text == "browser automation planned"
    # 两轮：第一轮模型要技能，第二轮带着技能正文作答。
    assert len(llm.requests) == 2
    tool_messages = [
        part
        for message in llm.requests[1].messages
        if message.role == "tool"
        for part in message.content
    ]
    assert tool_messages, "第二轮请求里没有 tool 消息，技能正文没有到达模型"
    body = "\n".join(str(getattr(part, "content", "")) for part in tool_messages)
    # 正文里的具体命令必须出现：这是「真的读到了那份文件」的证据，
    # 而不是返回了一句占位说明。
    assert "agent-browser skills get core" in body
    assert body.strip() in downloaded_skill
    # 没有任何一个 tool 结果是错误——空技能、未知技能都会以 isError 返回。
    assert not any(getattr(part, "isError", False) for part in tool_messages)


class _EmptyMCPManager:
    """没有 MCP 服务器的运行时：本用例只关心技能这一条链路。"""

    servers: dict = {}

    def get_tools(self):
        return {}
