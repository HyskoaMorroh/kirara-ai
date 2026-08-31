"""Regression tests for the ``reconnecting`` connection state.

Requirement 18.1 asks for five *distinguishable* situations, and the one that
was still collapsed is the one the original报障 describes: right after
``docker compose down && pull && up -d`` the panel said **未连接**.

That word was accurate for exactly one of two cases and wrong for the other:

- the OneBot implementation is gone for good (its container failed, the address
  changed) — nothing will happen without an operator;
- the OneBot implementation dropped its reverse WebSocket a moment ago and will
  dial back in on its own within seconds — *nothing needs to be done*.

Reporting both as ``disconnected`` is what turned a normal restart into a
support question: an operator who reads 未连接 goes and re-checks the token and
the address, which were never wrong. ``reconnecting`` is a "wait" state; every
other non-connected state in this model is a "go fix something" state.

The state is **time-bounded on purpose**. A link that has been "reconnecting"
for ten minutes is not reconnecting, it is down, and continuing to display a
wait state there would be the same defect with a friendlier word.
"""

from __future__ import annotations

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig


def make_adapter(config: OneBotConfig | None = None) -> OneBotAdapter:
    container = DependencyContainer()
    container.register(OneBotConfig, config or OneBotConfig())
    return Inject(container).create(OneBotAdapter)()


def _connected_then_dropped(now: float = 100.0) -> OneBotAdapter:
    """An adapter that was connected and just lost its only connection."""
    adapter = make_adapter()
    adapter._started = True
    adapter.connections["100"] = {"last_heartbeat": now}
    assert adapter.get_health_snapshot(now=now).status == "connected"
    # 上游发来 lifecycle disconnect：连接被摘掉，随后开启重连宽限期。
    adapter.connections.pop("100", None)
    adapter._connection_status = "disconnected"
    adapter._last_disconnect_reason = "upstream_lifecycle_disconnect"
    adapter._note_upstream_disconnected(now=now)
    return adapter


def test_reconnecting_is_a_declared_status_value():
    from kirara_ai.im.adapter import AdapterHealthSnapshot

    # A consumer that switches on the literal needs the value to exist in the
    # schema, otherwise the state can never leave the adapter.
    annotation = AdapterHealthSnapshot.model_fields["status"].annotation
    assert "reconnecting" in getattr(annotation, "__args__", ())


def test_upstream_drop_reports_reconnecting_not_disconnected():
    adapter = _connected_then_dropped(now=100.0)

    snapshot = adapter.get_health_snapshot(now=100.5)

    # 这是本条需求的核心：刚掉线且还在等上游回连，处置是「什么都不做」。
    assert snapshot.status == "reconnecting"
    assert snapshot.last_disconnect_reason == "upstream_lifecycle_disconnect"
    assert snapshot.connected_account_count == 0


def test_reconnecting_expires_into_disconnected():
    adapter = _connected_then_dropped(now=100.0)

    grace = adapter.config.reconnect_grace_seconds
    assert adapter.get_health_snapshot(now=100.0 + grace - 0.01).status == "reconnecting"

    # 超过宽限期就不再是「稍等」，把它继续显示成等待状态等于换个措辞掩盖故障。
    assert adapter.get_health_snapshot(now=100.0 + grace + 0.01).status == "disconnected"


def test_reconnect_success_clears_the_reconnecting_window():
    adapter = _connected_then_dropped(now=100.0)
    assert adapter.get_health_snapshot(now=100.5).status == "reconnecting"

    adapter.connections["100"] = {"last_heartbeat": 101.0}
    adapter._clear_reconnect_window()

    snapshot = adapter.get_health_snapshot(now=101.0)
    assert snapshot.status == "connected"
    # 恢复之后旧的断线原因不能继续显示：它描述的不再是当前状态。
    assert snapshot.last_disconnect_reason is None


def test_clean_stop_is_still_disconnected_not_reconnecting():
    adapter = _connected_then_dropped(now=100.0)

    # 主动停适配器不会自己回来，报成「正在重连」是一句不会兑现的承诺。
    adapter._started = False

    assert adapter.get_health_snapshot(now=100.5).status == "disconnected"


def test_credential_rejection_outranks_the_reconnect_window():
    adapter = _connected_then_dropped(now=100.0)

    # 令牌被拒是操作者能直接修的原因，被「正在重连」盖掉会让他一直等下去。
    adapter._connection_status = "credential_rejected"
    adapter._last_disconnect_reason = "access_token_mismatch"

    snapshot = adapter.get_health_snapshot(now=100.5)
    assert snapshot.status == "credential_rejected"
    assert snapshot.last_disconnect_reason == "access_token_mismatch"


def test_never_connected_adapter_does_not_report_reconnecting():
    adapter = make_adapter()
    adapter._started = True

    # 从未连上过就没有「重连」这回事，只有「还在等第一次连接」。
    assert adapter.get_health_snapshot(now=10.0).status == "waiting"


def test_reconnect_grace_seconds_zero_disables_the_window():
    adapter = make_adapter(OneBotConfig(reconnect_grace_seconds=0.0))
    adapter._started = True
    adapter.connections["100"] = {"last_heartbeat": 100.0}
    assert adapter.get_health_snapshot(now=100.0).status == "connected"
    adapter.connections.pop("100", None)
    adapter._connection_status = "disconnected"
    adapter._note_upstream_disconnected(now=100.0)

    # 关掉这个窗口必须拿回逐字节一致的旧行为，而不是留一个 0 秒的空窗。
    assert adapter.get_health_snapshot(now=100.0).status == "disconnected"


def test_readiness_treats_reconnecting_as_a_wait_state():
    from kirara_ai.web.api.system.readiness import _im_availability

    class _Adapter:
        def get_health_snapshot(self):
            from kirara_ai.im.adapter import AdapterHealthSnapshot

            return AdapterHealthSnapshot(
                status="reconnecting",
                last_disconnect_reason="upstream_lifecycle_disconnect",
            )

    class _Manager:
        adapters = {"onebot-main": _Adapter()}

        def is_adapter_running(self, name: str) -> bool:
            return True

    class _Item:
        name = "onebot-main"
        enable = True

    class _Config:
        ims = [_Item()]

    check = _im_availability(_Config(), _Manager())

    assert check.status == "warn"
    # 自检必须单独计这一类，否则「等几秒就好」与「上游没了」在同一个数字里。
    assert check.evidence["reconnecting_count"] == 1
    assert check.evidence["disconnected_count"] == 0
    assert "重连" in check.summary
