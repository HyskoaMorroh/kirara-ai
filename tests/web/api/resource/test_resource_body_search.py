"""按正文搜索文本资源要在服务器侧做，而不是把正文全量发给浏览器（需求 10）。

需求 10 点名「Claude 提示词管理」，参考界面那一页的搜索框「支持按名称、描述或
**内容**检索」（`docs/superpowers/plans/ccs-ui-notes.md` 的
`Image_2026-08-23_033206_571`）。

本项目的搜索是前端过滤（`resourceFilter.ts` 的 `matchesResourceKeyword`），匹配面
只有 `resource_id` / `name` / `description`——因为 `GET /resources` **不返回正文**：
`list_resources()` 只给注册表元数据。

这不是「少匹配一个字段」。提示词这个类型的**全部内容就是正文**，而名称与描述都是
用户自己随手填的一行字。装了十几条提示词之后，「哪一条里写了『先给结论』」这个
问题只能靠逐条点开看——而那正是搜索框存在的理由。

不能靠「让列表接口顺带返回正文」解决：`read_entry` 每次读取都要重新校验摘要
（读清单、读文件、算 SHA-256），列表接口对每条资源都做一遍等于把一次列表请求
变成 N 次全文件哈希；正文本身也可能有几十 KB，几十条就是一次几 MB 的响应，
而其中绝大部分与当次搜索无关。更要紧的是**提示词正文会包含用户写进去的敏感规则**，
把它无条件塞进每一次列表响应，等于让一个只想看清单的请求把全部正文都取回浏览器。

所以正确形态是**服务器侧按关键词过滤**：`GET /resources?query=...` 在服务器上读
正文、只返回命中的那几条元数据（仍然不含正文）。

这组测试锁住的边界：

1. 命中名称、描述、ID 与**正文**四个面。
2. 响应里**不含正文**——搜索是为了缩小清单，不是为了取回内容。
3. 只对不含可执行内容的类型读正文（prompt / memory / session）。skill 与 hook 的
   正文是行为声明，把它们并进关键词搜索会让一次搜索读遍所有 hook 命令行。
4. 读正文失败（文件被篡改、摘要不匹配）时**跳过那一条**，不让整个列表 500：
   一条坏资源不该让「列出资源」这个动作不可用。
5. 大小写不敏感，中文按子串。
"""

from __future__ import annotations

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


def _api(tmp_path: Path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(AuthService, MockAuthService(creator=True))
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


def _author(lifecycle: ResourceLifecycleService, resource_id: str, **overrides) -> None:
    payload = {
        "resource_id": resource_id,
        "resource_type": "prompt",
        "content": "占位正文\n",
        "name": resource_id,
        "description": "",
    }
    payload.update(overrides)
    lifecycle.author_document(**payload)


async def _search(client, query: str, **params) -> list[dict]:
    from urllib.parse import urlencode

    search = urlencode({"query": query, **params})
    response = await client.get(f"/api/resources?{search}", headers=_headers())
    assert response.status_code == 200, await response.get_data(as_text=True)
    return await response.get_json()


@pytest.mark.asyncio
async def test_it_matches_the_body(tmp_path: Path):
    """正文命中是这条特性的全部理由：名称与描述只是用户随手填的一行字。"""
    client, lifecycle = _api(tmp_path)
    _author(lifecycle, "prompt.a", content="你是一名助手。先给结论，再给依据。\n")
    _author(lifecycle, "prompt.b", content="你是一名翻译。保留原文语气。\n")

    hits = await _search(client, "先给结论")

    assert [item["resource_id"] for item in hits] == ["prompt.a"]


@pytest.mark.asyncio
async def test_the_response_never_carries_the_body(tmp_path: Path):
    """搜索是为了缩小清单，不是为了取回内容。

    正文可能有几十 KB，且包含用户写进去的规则。把它塞进列表响应，
    等于让一个只想看清单的请求把全部正文都取回浏览器。
    """
    client, lifecycle = _api(tmp_path)
    _author(lifecycle, "prompt.a", content="一段不该出现在列表响应里的正文\n")

    hits = await _search(client, "不该出现")

    assert len(hits) == 1
    serialized = str(hits)
    assert "不该出现在列表响应里" not in serialized
    assert "content" not in hits[0]


@pytest.mark.asyncio
async def test_it_still_matches_name_id_and_description(tmp_path: Path):
    """加了正文这一面，原有三面不能丢。"""
    client, lifecycle = _api(tmp_path)
    _author(lifecycle, "prompt.office", name="办公助手", description="邮件与会议")

    assert len(await _search(client, "office")) == 1, "ID 面失效"
    assert len(await _search(client, "办公助手")) == 1, "名称面失效"
    assert len(await _search(client, "会议")) == 1, "描述面失效"


@pytest.mark.asyncio
async def test_matching_is_case_insensitive(tmp_path: Path):
    client, lifecycle = _api(tmp_path)
    _author(lifecycle, "prompt.a", content="Always cite Documentation.\n")

    assert len(await _search(client, "DOCUMENTATION")) == 1
    assert len(await _search(client, "documentation")) == 1


@pytest.mark.asyncio
async def test_an_empty_query_returns_everything(tmp_path: Path):
    """「没在搜」不等于「搜不到」。"""
    client, lifecycle = _api(tmp_path)
    _author(lifecycle, "prompt.a")
    _author(lifecycle, "prompt.b")

    assert len(await _search(client, "   ")) == 2


@pytest.mark.asyncio
async def test_the_type_filter_still_applies(tmp_path: Path):
    """搜索与类型筛选是两个维度，必须能同时用。"""
    client, lifecycle = _api(tmp_path)
    _author(lifecycle, "prompt.a", content="共同的词\n")
    _author(lifecycle, "memory.a", resource_type="memory", content="共同的词\n")

    hits = await _search(client, "共同的词", type="prompt")

    assert [item["resource_id"] for item in hits] == ["prompt.a"]


@pytest.mark.asyncio
async def test_executable_types_are_not_searched_by_body(tmp_path: Path):
    """skill 与 hook 的正文是行为声明，不进关键词搜索。

    把它们并进来会让一次搜索读遍所有 hook 命令行——那既慢，也把
    「找一条提示词」变成一次对全部可执行声明的全文检索。
    它们仍然可以按 ID / 名称 / 描述命中。
    """
    client, lifecycle = _api(tmp_path)
    catalog = ResourceCatalogService(lifecycle)
    catalog.install("hook:ai-debug")

    # `audit_hook_command` 只出现在 hook 的正文（`hook.json`）里。
    assert await _search(client, "audit_hook_command") == []
    # 但按 ID 仍然找得到。
    assert len(await _search(client, "ai-debug")) == 1


@pytest.mark.asyncio
async def test_an_unreadable_body_does_not_break_the_listing(tmp_path: Path):
    """一条坏资源不该让「列出资源」这个动作不可用。

    `read_entry` 会在摘要不匹配时抛错。搜索时逐条读正文，因此一个被篡改的
    文件会让整个列表 500——而那时用户既看不到清单，也无从知道是哪一条坏了。
    """
    client, lifecycle = _api(tmp_path)
    _author(lifecycle, "prompt.good", content="可以读到的正文\n")
    _author(lifecycle, "prompt.broken", content="将被篡改\n")

    resource = lifecycle.get_resource("prompt.broken")
    entry = (
        lifecycle.installed_path
        / "prompt.broken"
        / resource["current_version"]
        / "PROMPT.md"
    )
    entry.write_text("篡改后的正文，摘要不再匹配\n", encoding="utf-8")

    hits = await _search(client, "可以读到")

    assert [item["resource_id"] for item in hits] == ["prompt.good"]


@pytest.mark.asyncio
async def test_a_bounded_query_is_enforced(tmp_path: Path):
    """无界关键词会让每条资源都做一次超长子串匹配。"""
    client, _ = _api(tmp_path)

    response = await client.get(
        f"/api/resources?query={'x' * 500}", headers=_headers()
    )

    assert response.status_code == 400
