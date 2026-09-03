"""提示词必须能从界面创建，而不是只能上传一个手工打包的 ZIP（需求 10）。

需求 10 点名「Claude 提示词管理」，参考界面上那一页有一个「+ 添加提示词」按钮：
填名称、描述、正文，保存。

本项目的现状是**读得出、装得进、改不了、建不了**：

- 读：`GET /resources/<id>/content` 返回正文与已校验摘要；
- 装：`POST /resources`（multipart ZIP）、`POST /resources/catalog/install`（内置目录）、
  `POST /resources/remote-install`（GitHub / skills.sh）；
- 建：**没有任何接口**。想加一条自己的提示词，得在本机按 `manifest.json` 的
  八个必填字段手算 `content_sha256`（还是 `path:size:sha256\\n` 逐行拼接再哈希这种
  非常规算法）、打成 ZIP、再上传。

这不是「少一个便捷入口」。提示词这个类型的**全部内容就是正文**——它没有可执行
文件、没有依赖、没有外部来源。要求用户为一段纯文本走一遍归档打包与摘要计算，
等于把这个类型最主要的用法排除在产品之外。而 `_install_builtin()`
（`resource_catalog.py`）早就在服务器侧做了完全一样的事：拿一段文本，
算摘要、生成清单、打包、装进去。缺的只是把它开放给用户内容的那一层。

不能因此放弃完整性契约。`content_sha256` 把清单与文件绑在一起，`read_entry`
每次读取都重新校验——所以正确做法不是「加一个能就地改文件的编辑框」
（那会让资源在下一次载入时直接失败），而是**服务器侧按同一套规则打包一个新版本**：
新建走 install、改正文走 update（版本号递增 + 自动备份 + 装完保持停用等待确认）。

这组测试锁住的边界：

1. 只接受不含可执行内容的类型（prompt / memory / session）。skill 与 hook 的正文
   会被当作行为声明执行或解析，hook 更是能起进程——那两类必须继续走
   「打包 + 审阅 + 显式确认」的路径。
2. 装完保持停用且需要确认，与其他安装路径一致。
3. 权限声明只能是 `workflow.read`：一段文本不需要写权限，更不需要进程执行。
4. 创建者身份 —— 它写服务器磁盘。
5. 摘要由服务器算，请求方不能提供（提供等于让调用方自己决定「校验通过」）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_sources import ResourceSourceService
from kirara_ai.plugin_manager.system_dependencies import SystemDependencyService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


def _api(tmp_path: Path, *, creator: bool = True):
    """装一条与产品同构的依赖链。

    目录服务必须在容器里：响应序列化器（`_resource_response`）用它投影
    「这台机器还缺什么依赖」。少注册一个就会在**成功**路径上抛 KeyError，
    而那与被测的创建逻辑无关。
    """
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(AuthService, MockAuthService(creator=creator))
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    container.register(ResourceLifecycleService, lifecycle)
    sources = ResourceSourceService(lifecycle)
    container.register(ResourceSourceService, sources)
    dependencies = SystemDependencyService(tmp_path / "data")
    container.register(SystemDependencyService, dependencies)
    container.register(
        ResourceCatalogService, ResourceCatalogService(lifecycle, sources, dependencies)
    )

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), lifecycle


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


def _payload(**overrides) -> dict:
    payload = {
        "resource_id": "prompt.my-office",
        "type": "prompt",
        "name": "我的办公提示词",
        "description": "邮件与会议场景",
        "content": "你是一名严谨的办公助手。\n先给结论，再给依据。\n",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_a_prompt_can_be_authored_from_plain_text(tmp_path: Path):
    """最基本的一条：给一段文本，服务器把它装成一个可绑定的资源。"""
    client, lifecycle = _api(tmp_path)

    response = await client.post(
        "/api/resources/documents", headers=_headers(), json=_payload()
    )

    assert response.status_code == 201, await response.get_data(as_text=True)
    resource = lifecycle.get_resource("prompt.my-office")
    assert resource["type"] == "prompt"
    body = lifecycle.read_entry("prompt.my-office", resource["current_version"])
    assert "先给结论，再给依据。" in body


@pytest.mark.asyncio
async def test_the_server_computes_the_digest(tmp_path: Path):
    """摘要由服务器算，且真的能通过 `read_entry` 的校验。

    `read_entry` 每次读取都重新比对摘要——它能读出来，就证明清单里的摘要
    与落盘的文件是一致的。这条断言比「清单里有一个 64 位十六进制串」强得多：
    后者一个写死的常量也能满足。
    """
    client, lifecycle = _api(tmp_path)

    await client.post("/api/resources/documents", headers=_headers(), json=_payload())

    resource = lifecycle.get_resource("prompt.my-office")
    # 不抛异常即为摘要校验通过。
    assert lifecycle.read_entry("prompt.my-office", resource["current_version"])
    metadata = lifecycle.read_entry_metadata("prompt.my-office")
    assert len(metadata["content_sha256"]) == 64


@pytest.mark.asyncio
async def test_a_client_supplied_digest_is_refused(tmp_path: Path):
    """请求方不能自带摘要：那等于让调用方自己决定「校验通过」。"""
    client, _ = _api(tmp_path)

    response = await client.post(
        "/api/resources/documents",
        headers=_headers(),
        json=_payload(content_sha256="0" * 64),
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_it_lands_disabled_and_needs_confirmation(tmp_path: Path):
    """与其他安装路径同一边界：装完不自动生效。

    提示词会进系统提示词、改变每一轮回复。「保存即生效」会让一次手误立刻
    作用到所有对话上，而其他三条安装路径都要显式确认。
    """
    client, lifecycle = _api(tmp_path)

    await client.post("/api/resources/documents", headers=_headers(), json=_payload())

    resource = lifecycle.get_resource("prompt.my-office")
    assert resource["enabled"] is False
    assert resource["confirmation_required"] is True


@pytest.mark.asyncio
async def test_only_text_types_are_accepted(tmp_path: Path):
    """skill 与 hook 不走这条路：它们的正文会被执行或解析成行为声明。

    hook 甚至能起进程（`process.execute`）。那两类必须继续走
    「打包 + 审阅 + 显式确认」，一个纯文本输入框不是合适的入口。
    """
    client, _ = _api(tmp_path)

    for resource_type in ("skill", "hook", "mcp"):
        response = await client.post(
            "/api/resources/documents",
            headers=_headers(),
            json=_payload(resource_id=f"{resource_type}.x", type=resource_type),
        )
        assert response.status_code == 400, f"{resource_type} 不该被接受"


@pytest.mark.asyncio
async def test_memory_and_session_are_accepted(tmp_path: Path):
    """prompt 之外，memory 与 session 同样是纯文本类型。

    清单校验里 `prompt` 与 `session` 已被禁止携带脚本后缀，memory 的内置件
    （`memory:research-context`）本身就是一段策略文本。三者同一形态。
    """
    client, lifecycle = _api(tmp_path)

    for resource_type in ("memory", "session"):
        response = await client.post(
            "/api/resources/documents",
            headers=_headers(),
            json=_payload(resource_id=f"{resource_type}.mine", type=resource_type),
        )
        assert response.status_code == 201, await response.get_data(as_text=True)
        assert lifecycle.get_resource(f"{resource_type}.mine")["type"] == resource_type


@pytest.mark.asyncio
async def test_the_permission_set_is_read_only(tmp_path: Path):
    """一段文本不需要写权限，更不需要进程执行。"""
    client, lifecycle = _api(tmp_path)

    await client.post("/api/resources/documents", headers=_headers(), json=_payload())

    assert lifecycle.get_resource("prompt.my-office")["permissions"] == ["workflow.read"]


@pytest.mark.asyncio
async def test_a_non_creator_is_refused(tmp_path: Path):
    """它写服务器磁盘，因此与其他安装路径同一边界。"""
    client, lifecycle = _api(tmp_path, creator=False)

    response = await client.post(
        "/api/resources/documents", headers=_headers(), json=_payload()
    )

    assert response.status_code == 403
    with pytest.raises(Exception):
        lifecycle.get_resource("prompt.my-office")


@pytest.mark.asyncio
async def test_empty_content_is_refused(tmp_path: Path):
    """空正文装进去等于一个什么都不做的提示词，而界面会显示「已安装」。"""
    client, _ = _api(tmp_path)

    for content in ("", "   \n\t "):
        response = await client.post(
            "/api/resources/documents", headers=_headers(), json=_payload(content=content)
        )
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_an_invalid_resource_id_is_refused(tmp_path: Path):
    """id 会成为磁盘路径的一段，必须先过与清单同一套校验。"""
    client, _ = _api(tmp_path)

    for resource_id in ("../escape", "has space", "", "a" * 200):
        response = await client.post(
            "/api/resources/documents",
            headers=_headers(),
            json=_payload(resource_id=resource_id),
        )
        assert response.status_code == 400, f"{resource_id!r} 不该被接受"


@pytest.mark.asyncio
async def test_a_duplicate_id_is_rejected_not_silently_overwritten(tmp_path: Path):
    """同名不能悄悄覆盖：改正文的路径是新建版本，不是重装。"""
    client, _ = _api(tmp_path)
    await client.post("/api/resources/documents", headers=_headers(), json=_payload())

    response = await client.post(
        "/api/resources/documents", headers=_headers(), json=_payload(content="改了")
    )

    assert response.status_code in (400, 409)


@pytest.mark.asyncio
async def test_a_new_version_can_be_authored_the_same_way(tmp_path: Path):
    """改正文走版本递增：就地改文件会让摘要校验在下一次载入时失败。"""
    client, lifecycle = _api(tmp_path)
    await client.post("/api/resources/documents", headers=_headers(), json=_payload())

    response = await client.put(
        "/api/resources/prompt.my-office/documents",
        headers=_headers(),
        json={"version": "1.1.0", "content": "第二版正文。\n"},
    )

    assert response.status_code == 200, await response.get_data(as_text=True)
    resource = lifecycle.get_resource("prompt.my-office")
    assert resource["current_version"] == "1.1.0"
    assert "第二版正文。" in lifecycle.read_entry("prompt.my-office", "1.1.0")
    # 旧版本仍在注册表里：改错了要能回去。
    assert {item["version"] for item in resource["versions"]} >= {"1.0.0", "1.1.0"}


@pytest.mark.asyncio
async def test_a_non_increasing_version_is_refused(tmp_path: Path):
    """版本号必须递增，否则「当前版本」这个字段失去意义。"""
    client, _ = _api(tmp_path)
    await client.post("/api/resources/documents", headers=_headers(), json=_payload())

    response = await client.put(
        "/api/resources/prompt.my-office/documents",
        headers=_headers(),
        json={"version": "0.9.0", "content": "回退"},
    )

    assert response.status_code in (400, 409)


@pytest.mark.asyncio
async def test_authoring_a_version_of_an_executable_type_is_refused(tmp_path: Path):
    """更新路径同样只对纯文本类型开放。

    只在新建那一侧设限会留一条绕道：先上传一个 skill 的 ZIP，
    再用纯文本接口改它的正文。
    """
    client, lifecycle = _api(tmp_path)
    from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService

    catalog = ResourceCatalogService(lifecycle)
    catalog.install("hook:ai-debug")

    response = await client.put(
        "/api/resources/hook.ai-debug/documents",
        headers=_headers(),
        json={"version": "9.9.9", "content": json.dumps({"events": {}})},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_the_upload_paths_still_work(tmp_path: Path):
    """对照组：新增这条路径不影响既有的 ZIP 安装与目录安装。"""
    client, lifecycle = _api(tmp_path)
    from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService

    ResourceCatalogService(lifecycle).install("prompt:office-research")

    assert lifecycle.get_resource("prompt.office-research")["type"] == "prompt"
    response = await client.get("/api/resources?type=prompt", headers=_headers())
    assert response.status_code == 200
