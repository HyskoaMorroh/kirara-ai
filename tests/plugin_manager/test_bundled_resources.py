"""随包资源必须在**离线**环境里装得上，且不能默认放开对 VPS 的操作能力。

为什么需要这一组
--------------
需求 4 要把本机（Claude Code / Codex / VS Code）在用的插件预置进本项目，
「拉取镜像后默认装好」。这带来两个必须同时成立、方向相反的要求：

1. **装得上**：不能出网。运行时镜像里没有 Node、没有 `uvx`，也不该在启动时
   依赖 github.com 可达——`ensure_builtins()` 跑在启动路径上。
2. **不放权**：预置一批插件不等于默认开放它们。需求 10 与 11 的边界是
   「只有创建者能通过插件改服务器内容」，而这条边界由
   `principal_can_control_agent()` 与 `creator_channel_identities` 共同兑现。
   预置之后这条边界必须**仍然**拦得住，否则等于默认放开。

这一组测试锁住的边界
------------------
1. 每一条 `bundled_dir` 条目都能在完全离线的临时目录里装上。
2. 随包目录里不含脚本与二进制——它们在这个镜像里跑不起来，
   装进去只会得到一个「启用了但用不了」的资源。
3. 随包资源的清单摘要与校验端算法一致（多文件时才暴露的那一类）。
4. MCP 模板一律不带 `env` 值，不带本机绝对路径。
5. 预置不改变权限边界：默认空白名单下，IM 渠道拿不到创建者身份。
"""

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from kirara_ai.plugin_manager.resource_catalog import (
    ResourceCatalogService,
    _BUILTINS,
)
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService

BUNDLED_ROOT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "kirara_ai"
    / "plugin_manager"
    / "bundled"
)

BUNDLED_ITEMS = tuple(item for item in _BUILTINS if item.get("bundled_dir"))

#: 在运行时镜像里跑不起来的后缀。
#:
#: 镜像只装了 Python、ffmpeg 与 libmagic1；`.sh` 要 shell 工具链、`.js`/`.mjs`
#: 要 Node、`.ps1` 要 PowerShell。带这些文件的技能装上之后界面显示正常，
#: 而用户一使用就失败——比不装更糟。
FORBIDDEN_SUFFIXES = frozenset(
    {".sh", ".ps1", ".bat", ".cmd", ".js", ".mjs", ".cjs", ".ts", ".exe", ".dll", ".jar"}
)


def catalog(root: pathlib.Path) -> ResourceCatalogService:
    return ResourceCatalogService(ResourceLifecycleService(root))


class TestEveryBundledItemInstallsOffline:
    def test_there_is_a_meaningful_number_of_bundled_items(self):
        """空集合会让下面每一条参数化用例都「通过」——那是最糟的绿。"""
        assert len(BUNDLED_ITEMS) >= 30

    @pytest.mark.parametrize(
        "catalog_id",
        [item["catalog_id"] for item in BUNDLED_ITEMS],
    )
    def test_it_installs_without_any_network_call(self, catalog_id: str):
        with tempfile.TemporaryDirectory() as tmp:
            record = catalog(pathlib.Path(tmp)).install(catalog_id)

        assert record["resource_id"] == catalog_id.replace(":", ".", 1)
        assert record["current_version"]

    def test_a_multi_file_resource_keeps_its_directory_layout(self):
        """多文件资源是摘要算法不一致唯一会暴露的地方。

        校验端 `_content_hash()` 按路径排序算摘要；打包端不排序时两侧对同一批
        文件得出不同的 `content_sha256`，安装被判为
        「resource content digest does not match manifest」。
        **单文件资源上两种顺序恰好一致**，所以这个错在提示词、记忆、MCP 上
        完全没有症状——只有多文件技能才会暴露。
        """
        multi = [
            item
            for item in BUNDLED_ITEMS
            if len(
                [
                    path
                    for path in (BUNDLED_ROOT / str(item["bundled_dir"])).rglob("*")
                    if path.is_file()
                ]
            )
            > 1
        ]
        assert multi, "随包资源里必须至少有一个多文件的，否则这条护栏形同虚设"

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            service = catalog(root)
            item = multi[0]
            service.install(str(item["catalog_id"]))
            resource_id = str(item["catalog_id"]).replace(":", ".", 1)
            installed = root / "resources/installed" / resource_id / str(item["version"])
            manifest = json.loads((installed / "manifest.json").read_text(encoding="utf-8"))
            # 目录扫描必须在 `with` 块**内**：块一退出临时目录就被删掉，
            # 那时 `rglob` 返回空列表，断言会把「目录已删」报成「文件没落盘」——
            # 一个与被测行为无关的失败。
            on_disk = sorted(
                path.relative_to(installed).as_posix()
                for path in installed.rglob("*")
                if path.is_file() and path.name != "manifest.json"
            )

        assert on_disk == sorted(entry["path"] for entry in manifest["files"])
        # 至少有一条带子目录的路径，否则这条用例退化成单文件场景。
        assert any("/" in path for path in on_disk), (
            f"{item['catalog_id']} 没有子目录文件，换一个多层级的随包资源来钉这条"
        )


class TestBundledContentIsRunnableInThisImage:
    @pytest.mark.parametrize(
        "catalog_id,bundled_dir",
        [(item["catalog_id"], item["bundled_dir"]) for item in BUNDLED_ITEMS],
    )
    def test_it_ships_no_script_or_binary(self, catalog_id: str, bundled_dir: str):
        directory = BUNDLED_ROOT / bundled_dir
        offenders = [
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in FORBIDDEN_SUFFIXES
        ]

        assert not offenders, (
            f"{catalog_id} 带了这个镜像跑不起来的文件：{offenders[:5]}"
        )

    @pytest.mark.parametrize(
        "catalog_id,bundled_dir,entry",
        [(item["catalog_id"], item["bundled_dir"], item["entry"]) for item in BUNDLED_ITEMS],
    )
    def test_the_declared_entry_file_exists(
        self, catalog_id: str, bundled_dir: str, entry: str
    ):
        """入口缺失时 `install()` 才会报错——那时用户已经点了安装。"""
        assert (BUNDLED_ROOT / bundled_dir / entry).is_file(), (
            f"{catalog_id} 声明的入口 {entry} 不存在"
        )

    @pytest.mark.parametrize(
        "catalog_id,description",
        [(item["catalog_id"], item.get("description", "")) for item in BUNDLED_ITEMS],
    )
    def test_it_has_a_searchable_description(self, catalog_id: str, description: str):
        """描述为空的资源在搜索里永远命中不了。

        本项目修过这个形状：过滤谓词读 `name` / `description`，而记录里没有
        这两个字段，于是三个匹配面里两个从未命中过任何东西。
        """
        assert description.strip(), f"{catalog_id} 没有描述"
        assert description.strip() != ">", f"{catalog_id} 的描述只是一个 YAML 折叠标记"


class TestMcpTemplatesCarryNoLocalSecretsOrPaths:
    @pytest.mark.parametrize(
        "item",
        [item for item in _BUILTINS if item["type"] == "mcp"],
        ids=lambda item: str(item["catalog_id"]),
    )
    def test_the_template_has_no_prefilled_env_values(self, item):
        """模板会写进 `data/resources/` 并可能随备份导出，预填令牌会跟着走。"""
        server = (item.get("content") or {}).get("server") or {}

        assert server.get("env") in ({}, None), (
            f"{item['catalog_id']} 预填了 env，凭据会随备份导出"
        )

    @pytest.mark.parametrize(
        "item",
        [item for item in _BUILTINS if item["type"] == "mcp"],
        ids=lambda item: str(item["catalog_id"]),
    )
    def test_the_command_is_not_a_machine_specific_absolute_path(self, item):
        """本机绝对路径在别人的机器与容器里都不存在。

        `node_repl`（Codex 自带运行时）与 `context-mode`（全局 npm 目录）
        因此**不被收进**内置模板：预置一条死配置，而界面上它与其他模板同形。
        """
        server = (item.get("content") or {}).get("server") or {}
        command = str(server.get("command") or "")

        assert not command.startswith("/"), f"{item['catalog_id']} 用了绝对路径"
        assert ":\\" not in command, f"{item['catalog_id']} 用了 Windows 绝对路径"
        for argument in server.get("args") or []:
            text = str(argument)
            assert ":\\" not in text, f"{item['catalog_id']} 的 args 含本机路径：{text}"
