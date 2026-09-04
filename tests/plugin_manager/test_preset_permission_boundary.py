"""预置一批插件不等于默认放开它们。

为什么需要这一组
--------------
需求 4 把本机在用的插件预置进本项目（36 条随包资源：31 个技能、5 个角色提示词），
需求 10 与 11 的边界是「只有创建者能通过插件改服务器内容，其他使用者仍得到正常
回复」。这两件事方向相反：预置得越多，「默认不放权」这条边界就越要有证据。

这一组不重复 `tests/workflow/test_creator_channel_identity.py`（那里验证的是
「哪个身份能拿到 principal」），而是验证**预置之后**：

1. 随包资源里没有任何一条声明了能改服务器的权限；
2. 会起进程的 MCP 模板都带 `runtime_dependency`，因此在没有该命令的机器上
   显示为「缺依赖」而不是静默失败；
3. 默认配置下白名单是空的——预置不附带任何身份声明；
4. `principal_can_control_agent` 在没有 principal 时返回 False，
   也就是 IM 渠道默认拿不到能操作 VPS 的工具。

第 3 与第 4 条是「默认安全」的两半：白名单空 → 没有 principal → 工具列表为空。
少任何一半，预置就等于默认开放。
"""

from __future__ import annotations

import pytest

from kirara_ai.agent_runtime.core import principal_can_control_agent
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.plugin_manager.resource_catalog import _BUILTINS
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context

BUNDLED_ITEMS = tuple(item for item in _BUILTINS if item.get("bundled_dir"))

#: 能改服务器内容或执行文件操作的权限名。随包资源一条都不许声明。
#:
#: 判据是权限名而不是资源类型：一个 `prompt` 也可以声明 `process.execute`，
#: 而那正是「看起来无害的资源拿到了危险权限」的形状。
DANGEROUS_PERMISSIONS = frozenset(
    {"process.execute", "filesystem.write", "workflow.write", "host.control"}
)


class TestBundledResourcesDeclareNoDangerousPermission:
    @pytest.mark.parametrize(
        "catalog_id,permissions",
        [(item["catalog_id"], tuple(item.get("permissions") or ())) for item in BUNDLED_ITEMS],
        ids=lambda value: str(value) if isinstance(value, str) else "",
    )
    def test_it_only_asks_for_read_access(self, catalog_id: str, permissions: tuple):
        offenders = sorted(set(permissions) & DANGEROUS_PERMISSIONS)

        assert not offenders, (
            f"{catalog_id} 声明了能改服务器的权限 {offenders}；"
            "随包资源默认就装上，因此它们只能是只读的"
        )

    def test_the_only_process_executing_builtin_is_the_audit_hook(self):
        """`hook:ai-debug` 是唯一带 `process.execute` 的内置，且它不是随包资源。

        这条断言的作用是**说明例外**：审计 Hook 确实要起进程（跑
        `python -m kirara_ai.agent_runtime.audit_hook_command`），但它跑的是
        本项目自己的模块、不接受外部输入。新增一个带 `process.execute` 的内置
        会让这条红，并要求回答「为什么它需要起进程」。
        """
        executing = sorted(
            str(item["catalog_id"])
            for item in _BUILTINS
            if "process.execute" in (item.get("permissions") or ())
        )

        assert executing == ["hook:ai-debug"]


class TestProcessSpawningTemplatesDeclareTheirDependency:
    @pytest.mark.parametrize(
        "item",
        [item for item in _BUILTINS if item["type"] == "mcp"],
        ids=lambda item: str(item["catalog_id"]),
    )
    def test_a_stdio_template_says_what_launches_it(self, item):
        """缺这条声明时，用户点「启用」只会看到「连接失败 / 工具数 0」。

        运行时镜像没有 Node 也没有 `uvx`，所以「模板已预置」与「这个 MCP 能跑」
        是两件事。声明了 `runtime_dependency`，界面才能说出「这台机器缺 npx」，
        而不是让人去查网络、查配置、查 API Key。
        """
        server = (item.get("content") or {}).get("server") or {}
        if str(server.get("type")) != "stdio":
            pytest.skip("非 stdio 传输不靠本机命令拉起")

        assert item.get("runtime_dependency"), (
            f"{item['catalog_id']} 是 stdio 模板但没声明 runtime_dependency"
        )


class TestPresetsDoNotGrantAnyIdentity:
    def test_the_creator_allowlist_is_empty_by_default(self):
        """预置插件不附带任何身份声明。

        这是「默认安全」的前一半：白名单为空时，IM 渠道拿不到 principal。
        """
        config = GlobalConfig()

        assert config.agent_runtime.creator_channel_identities == []

    def test_without_a_principal_nothing_can_control_an_agent(self):
        """后一半：没有 principal 时门禁一律拒绝。

        `owner_subject` 非空也不行——这条断言防的是「把 owner 设成非空
        就等于放行」这种误解。
        """
        assert principal_can_control_agent("some-owner-subject") is False

    def test_a_non_creator_principal_is_still_refused(self):
        """带 principal 但 `is_creator` 为假时同样拒绝。"""
        with runtime_principal_context(
            RuntimePrincipal(subject="regular-user", is_creator=False)
        ):
            assert principal_can_control_agent("regular-user") is False

    def test_a_creator_principal_must_also_match_the_owner(self):
        """是创建者但不是这个 Agent 的属主时拒绝。

        两个条件都要满足，避免「一个创建者能操作别人的 Agent」。
        """
        with runtime_principal_context(
            RuntimePrincipal(subject="creator-a", is_creator=True)
        ):
            assert principal_can_control_agent("creator-b") is False
            assert principal_can_control_agent("creator-a") is True
