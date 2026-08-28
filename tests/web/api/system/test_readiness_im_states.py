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


def make_manager(snapshots: dict[str, AdapterHealthSnapshot | None]):
    manager = MagicMock()
    manager.is_adapter_running.side_effect = lambda name: name in snapshots
    adapters = {}
    for name, snapshot in snapshots.items():
        if snapshot is None:
            adapters[name] = SimpleNamespace()
        else:
            adapter = MagicMock()
            adapter.get_health_snapshot.return_value = snapshot
            adapters[name] = adapter
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
