"""每个 IM 适配器都必须自报连接状态，不能让 readiness 代替它假设健康。

`readiness.py` 对不实现 `AdapterHealthProvider` 的适配器走「按 connected 计数」
的兜底分支。Telegram 与 WeCom 此前正落在这条分支里：Token 失效、凭据换不出
access_token 的适配器在就绪检查里显示为健康。面板给出错误的安心，比不给状态更糟
——这正是 1.txt 18.3 明确禁止的「假连接状态」。
"""

from __future__ import annotations

import pytest

from kirara_ai.im.adapter import AdapterHealthProvider, AdapterHealthSnapshot


def test_every_shipped_adapter_declares_the_health_protocol():
    """新增适配器若忘了实现，这条测试会立刻指出来。"""
    from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
    from kirara_ai.plugins.im_qqbot_adapter.adapter import QQBotAdapter
    from kirara_ai.plugins.im_telegram_adapter.adapter import TelegramAdapter
    from kirara_ai.plugins.im_wecom_adapter.adapter import WecomAdapter

    for adapter_class in (OneBotAdapter, QQBotAdapter, TelegramAdapter, WecomAdapter):
        assert hasattr(adapter_class, "get_health_snapshot"), (
            f"{adapter_class.__name__} 未实现 get_health_snapshot，"
            "readiness 会把它按 connected 计数"
        )


def bare_telegram():
    from kirara_ai.plugins.im_telegram_adapter.adapter import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.me = None
    adapter.application = None
    adapter._outbox = None
    adapter._started = False
    adapter._ever_started = False
    adapter._last_disconnect_reason = None
    return adapter


def test_telegram_before_start_is_initializing_not_connected():
    snapshot = bare_telegram().get_health_snapshot()

    assert isinstance(snapshot, AdapterHealthSnapshot)
    assert snapshot.status == "initializing"
    assert snapshot.connected_account_count == 0


def test_telegram_started_without_identity_is_waiting():
    """长轮询没跑起来或 get_me 没成功时不得报 connected。"""
    adapter = bare_telegram()
    adapter._started = True

    assert adapter.get_health_snapshot().status == "waiting"


def test_telegram_polling_and_authenticated_is_connected():
    class _Updater:
        running = True

    class _App:
        updater = _Updater()

    adapter = bare_telegram()
    adapter._started = True
    adapter.application = _App()
    adapter.me = object()

    snapshot = adapter.get_health_snapshot()
    assert snapshot.status == "connected"
    assert snapshot.connected_account_count == 1
    assert snapshot.websocket_connected is True


def test_telegram_after_stop_is_disconnected_not_initializing():
    """停过一次之后不能再报「正在启动」——那会掩盖一次真实的停机。"""
    adapter = bare_telegram()
    adapter._started = True
    adapter._ever_started = True
    adapter._started = False

    assert adapter.get_health_snapshot().status == "disconnected"


def bare_wecom():
    from kirara_ai.plugins.im_wecom_adapter.adapter import WecomAdapter

    adapter = object.__new__(WecomAdapter)
    adapter.is_running = False
    adapter.api_delegate = None
    adapter._outbox = None
    adapter._ever_started = False
    adapter._last_disconnect_reason = None
    return adapter


def test_wecom_before_start_is_initializing():
    assert bare_wecom().get_health_snapshot().status == "initializing"


def test_wecom_started_without_api_delegate_is_waiting():
    """凭据换不出 access_token 时 api_delegate 为空，此时绝不能报 connected。"""
    adapter = bare_wecom()
    adapter.is_running = True

    assert adapter.get_health_snapshot().status == "waiting"


def test_wecom_with_api_delegate_is_connected():
    adapter = bare_wecom()
    adapter.is_running = True
    adapter.api_delegate = object()

    snapshot = adapter.get_health_snapshot()
    assert snapshot.status == "connected"
    assert snapshot.connected_account_count == 1


def test_wecom_after_stop_is_disconnected():
    adapter = bare_wecom()
    adapter._ever_started = True

    assert adapter.get_health_snapshot().status == "disconnected"


@pytest.mark.parametrize("factory", [bare_telegram, bare_wecom])
def test_a_broken_outbox_never_breaks_the_health_snapshot(factory):
    """观测不能成为新的失败点：投递计数抛错只应丢掉这一项。"""

    class Hostile:
        def status_counts(self):
            raise RuntimeError("outbox exploded")

    adapter = factory()
    adapter._outbox = Hostile()

    snapshot = adapter.get_health_snapshot()

    assert snapshot.outbox is None
    assert snapshot.status in {"initializing", "waiting", "disconnected", "connected"}


@pytest.mark.parametrize("factory", [bare_telegram, bare_wecom])
def test_the_snapshot_is_runtime_checkable_as_the_protocol(factory):
    """readiness 用 isinstance 判定，所以运行时协议匹配必须成立。"""
    assert isinstance(factory(), AdapterHealthProvider)
