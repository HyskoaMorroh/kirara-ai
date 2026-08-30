"""Readiness must classify a refused upstream apart from a slow one.

Before this, `_im_availability` counted only connected / waiting / disconnected /
stale and used `counts[snapshot.status] += 1` directly, so an adapter reporting a
newer status raised KeyError and took the whole readiness endpoint down with a
500. It also gave the same remediation text ("check the heartbeat") for a
rejected access token, which is the one case where waiting never helps.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from kirara_ai.config.global_config import GlobalConfig, IMConfig
from kirara_ai.im.adapter import AdapterHealthSnapshot
from kirara_ai.web.api.system.readiness import _im_availability


def make_config(*names: str) -> GlobalConfig:
    config = GlobalConfig()
    config.ims = [IMConfig(name=name, adapter="onebot", config={}, enable=True) for name in names]
    return config


class _HealthyAdapter:
    """一个真的实现了 `AdapterHealthProvider` 的替身。

    不能用裸 `MagicMock()`：Python 3.12 起，`runtime_checkable` Protocol 的
    `isinstance()` 改用 `inspect.getattr_static` 取属性，而 MagicMock 的
    `get_health_snapshot` 是在**被访问时**由 `__getattr__` 现造的，静态取不到。
    于是 `isinstance(adapter, AdapterHealthProvider)` 在 3.12 上返回 False，
    `_im_availability` 走进「没有健康快照能力」的分支、把这个适配器算成
    `connected`，本文件的九个用例集体失败——失败原因与被测行为毫无关系。

    CI 矩阵含 3.13，所以这不是本机特有现象。生产代码不受影响：真实适配器
    在类上定义了该方法，静态查找拿得到（四个适配器均已验证）。
    """

    def __init__(self, snapshot: AdapterHealthSnapshot):
        self._snapshot = snapshot

    def get_health_snapshot(self) -> AdapterHealthSnapshot:
        return self._snapshot


def make_manager(snapshots: dict[str, AdapterHealthSnapshot | None]):
    manager = MagicMock()
    manager.is_adapter_running.side_effect = lambda name: name in snapshots
    adapters = {}
    for name, snapshot in snapshots.items():
        if snapshot is None:
            # 故意不提供 `get_health_snapshot`：覆盖「适配器没有健康快照能力」
            # 这条分支，两个 Python 版本上都稳定为 False。
            adapters[name] = SimpleNamespace()
        else:
            adapters[name] = _HealthyAdapter(snapshot)
    manager.adapters = adapters
    return manager


def test_credential_rejected_gets_its_own_remediation():
    check = _im_availability(
        make_config("qq"),
        make_manager(
            {
                "qq": AdapterHealthSnapshot(
                    status="credential_rejected",
                    last_disconnect_reason="access_token_mismatch",
                )
            }
        ),
    )

    assert check.status == "warn"
    assert "拒绝" in check.summary
    assert "令牌" in check.remediation
    assert check.evidence["credential_rejected_count"] == 1


def test_upstream_refused_is_counted_separately_from_waiting():
    check = _im_availability(
        make_config("qq"),
        make_manager({"qq": AdapterHealthSnapshot(status="upstream_refused")}),
    )

    assert check.evidence["upstream_refused_count"] == 1
    assert check.evidence["waiting_count"] == 0


def test_initializing_is_not_reported_as_disconnected():
    check = _im_availability(
        make_config("qq"),
        make_manager({"qq": AdapterHealthSnapshot(status="initializing")}),
    )

    assert check.evidence["initializing_count"] == 1
    assert check.evidence["disconnected_count"] == 0


def test_a_plain_waiting_adapter_keeps_the_heartbeat_remediation():
    check = _im_availability(
        make_config("qq"),
        make_manager({"qq": AdapterHealthSnapshot(status="waiting")}),
    )

    assert "心跳" in check.remediation


def test_an_unknown_future_status_does_not_raise():
    """A newer adapter status must degrade, not take readiness down with a 500."""
    snapshot = AdapterHealthSnapshot(status="waiting")
    # Simulate an adapter built against a newer schema than this check knows.
    object.__setattr__(snapshot, "status", "some_future_state")

    check = _im_availability(make_config("qq"), make_manager({"qq": snapshot}))

    assert check.status == "warn"
    assert check.evidence["disconnected_count"] == 1


def test_all_connected_passes_with_no_remediation_work():
    check = _im_availability(
        make_config("qq"),
        make_manager(
            {"qq": AdapterHealthSnapshot(status="connected", connected_account_count=1)}
        ),
    )

    assert check.status == "pass"
    assert check.remediation == "无需处理"


def test_awaiting_scan_gets_scan_remediation_not_heartbeat_advice():
    """上游连上但 QQ 未登录时，处置是「去扫码」而不是「查连接」。

    不给这条区分，操作者会在一个其实只差扫码的实例上反复检查地址与 Token——
    这是「重启后显示未连接」这类报障里最常见的误诊方向。
    """
    from kirara_ai.im.qr_login import QRLoginSnapshot

    check = _im_availability(
        make_config("qq"),
        make_manager(
            {
                "qq": AdapterHealthSnapshot(
                    status="waiting",
                    qr_login=QRLoginSnapshot(state="waiting_scan"),
                )
            }
        ),
    )

    assert check.status == "warn"
    assert "扫码" in check.summary
    assert "二维码" in check.remediation
    assert check.evidence["qr_waiting_scan"] == 1


def test_an_expired_qr_also_points_at_the_newest_code():
    """过期同样归入「等待扫码」这一类，并提示不要扫旧码。"""
    from kirara_ai.im.qr_login import QRLoginSnapshot

    check = _im_availability(
        make_config("qq"),
        make_manager(
            {
                "qq": AdapterHealthSnapshot(
                    status="waiting",
                    qr_login=QRLoginSnapshot(state="expired"),
                )
            }
        ),
    )

    assert "扫码" in check.summary
    assert "过期" in check.remediation
    assert check.evidence["qr_expired"] == 1


def test_a_rejected_credential_still_outranks_an_awaiting_scan():
    """凭据被拒是更靠前的阻塞点：Token 不对时扫码也没用。"""
    from kirara_ai.im.qr_login import QRLoginSnapshot

    check = _im_availability(
        make_config("qq", "qq2"),
        make_manager(
            {
                "qq": AdapterHealthSnapshot(status="credential_rejected"),
                "qq2": AdapterHealthSnapshot(
                    status="waiting", qr_login=QRLoginSnapshot(state="waiting_scan")
                ),
            }
        ),
    )

    assert "拒绝" in check.summary
    assert "令牌" in check.remediation


def test_adapters_without_qr_state_add_no_evidence_keys():
    """没有扫码状态的适配器不得凭空造出 qr_* 字段。"""
    check = _im_availability(
        make_config("qq"),
        make_manager({"qq": AdapterHealthSnapshot(status="waiting")}),
    )

    assert not [key for key in check.evidence if key.startswith("qr_")]
    assert "心跳" in check.remediation
