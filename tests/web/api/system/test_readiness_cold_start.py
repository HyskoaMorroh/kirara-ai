"""冷启动窗口内 readiness 不能建议「去查心跳」（需求 1 的现场报障）。

反向 WebSocket 由 OneBot 实现主动拨入，而它要先冷启动 QQ 再完成登录——现场
日志里这一段超过 90 秒。这段时间里适配器报 `initializing`，而 readiness 的
`initializing` 计数虽然存在，摘要分支却把它落到兜底那一条上：
「检查 IM 适配器运行状态、登录状态和连接心跳」。

那是这个窗口里最不该给的建议。心跳没有问题，令牌没有问题，地址也没有问题——
上游还没起来而已。运维照着去查这三项，全部查完仍然「未连接」，于是开始怀疑
配置错了，而配置从一开始就是对的。

`reconnecting` 已经有专门的「等就行」文案，`initializing` 需要同一等级的待遇：
两者都是**不需要动手**的状态，与 `waiting`（等了很久也没人连进来，该查了）
是不同的处境。

分支顺序也要对：存储不可写、凭据被拒、等待扫码都比「正在启动」更具体，
先报它们；`initializing` 排在 `reconnecting` 旁边，兜底仍然留给真正的 `waiting`。
"""

from __future__ import annotations

from kirara_ai.im.adapter import AdapterHealthSnapshot
from kirara_ai.web.api.system.readiness import _im_availability
from tests.web.api.system.test_readiness_im_states import make_config, make_manager


def _check(status: str, **snapshot_kwargs):
    return _im_availability(
        make_config("qq"),
        make_manager({"qq": AdapterHealthSnapshot(status=status, **snapshot_kwargs)}),
    )


class TestInitializingGetsAWaitRemediation:
    def test_it_does_not_point_at_the_heartbeat(self):
        check = _check("initializing")

        assert "心跳" not in check.remediation, (
            "冷启动窗口里心跳没有问题，指向它会让运维查一件从来没错的事"
        )

    def test_it_says_the_upstream_has_not_dialled_in_yet(self):
        check = _check("initializing")

        assert "启动" in check.summary or "首次" in check.summary

    def test_the_remediation_says_to_wait(self):
        check = _check("initializing")

        assert "等待" in check.remediation

    def test_the_count_is_still_reported(self):
        check = _check("initializing")

        assert check.evidence["initializing_count"] == 1


class TestTheOrderOfSummaryBranches:
    def test_a_rejected_credential_still_wins_over_initializing(self):
        """凭据被拒要求操作者改配置，不能被「正在启动」的等待文案盖掉。"""
        check = _im_availability(
            make_config("qq", "qq2"),
            make_manager(
                {
                    "qq": AdapterHealthSnapshot(status="credential_rejected"),
                    "qq2": AdapterHealthSnapshot(status="initializing"),
                }
            ),
        )

        assert "被拒绝" in check.summary

    def test_a_plain_waiting_adapter_still_gets_the_heartbeat_remediation(self):
        """等了很久也没人连进来时仍然该去查——这条不能被新分支顺带改掉。"""
        check = _check("waiting")

        assert "心跳" in check.remediation
