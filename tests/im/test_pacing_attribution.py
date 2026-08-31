"""节流等待必须与上游发送耗时分开归因（需求 19.5）。

19.5 的原文点名五种原因不能混成一个「QQ 慢」：LLM 慢、QQ 热更新、**发送限流**、
WebSocket 重连、消息队列阻塞。而 `send_seconds` 目前是
`send_started → send_succeeded` 的整段墙钟时间，里面**同时**包含两件性质相反的事：

- 我们**主动等**的时间（`pacing.wait_before_page`，防刷屏，是设计行为）；
- 上游**真的慢**的时间（网络、QQ 服务端处理、限流拒绝后的重试）。

两者的处置正好相反：前者要调 `send_pacing` 配置，后者要查上游。混成一个数字时，
一条十页的回复因节流等了 20 秒，面板显示「平台发送 20 秒」——运维会去查 QQ，
而 QQ 那边什么问题都没有。反过来，上游真的慢时也会被归到「我们自己配的节流」上。

现场报障「系统显示成功到收到回复中间隔了很久」正是这个形态：日志里 Kirara 已经
`send_succeeded`，用户手机上还没收到——而那段时间的大头是节流。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kirara_ai.im.message import DELIVERY_STAGES, IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender


def _message() -> IMMessage:
    return IMMessage(
        sender=ChatSender.from_c2c_chat(user_id="u1", display_name="u1"),
        message_elements=[TextMessage("hello")],
    )


def test_pacing_is_a_declared_stage():
    """节流必须是一个**有名字**的阶段，而不是藏在 send 里的一段。

    不给它名字，就只能靠减法反推，而减法要求另外两个数都准——
    那是把一个可以直接测的量变成一个推断。
    """
    assert "send_pacing_waited" in DELIVERY_STAGES


def test_pacing_time_is_reported_separately_from_upstream_send():
    """同一次投递里，节流等待与上游发送必须是两个数。"""
    message = _message()
    base = datetime.now(timezone.utc)

    message.record_delivery_stage_at("send_started", base, adapter="onebot")
    # 十页回复，节流总共等了 18 秒；上游每页各花 0.2 秒。
    message.record_delivery_stage_at(
        "send_pacing_waited",
        base + timedelta(seconds=20),
        adapter="onebot",
        pacing_seconds=18.0,
    )
    message.record_delivery_stage_at(
        "send_succeeded", base + timedelta(seconds=20), adapter="onebot"
    )

    durations = message.delivery_durations()

    assert durations["send_pacing_seconds"] == pytest.approx(18.0)
    # 上游真实耗时 = 整段 20 秒减去我们主动等的 18 秒。
    assert durations["send_upstream_seconds"] == pytest.approx(2.0)
    # 整段仍然保留：它回答「用户等了多久」，与归因是两个问题。
    assert durations["send_seconds"] == pytest.approx(20.0)


def test_a_delivery_without_pacing_reports_null_not_zero():
    """没有节流记录时 `send_pacing_seconds` 必须缺失，而不是 0。

    `0` 是一个论断（「节流没等」），会让运维排除掉一个其实没有被测量的原因。
    关闭了节流的部署与「用的是不记录节流的第三方适配器」必须可区分。
    """
    message = _message()
    base = datetime.now(timezone.utc)
    message.record_delivery_stage_at("send_started", base, adapter="telegram")
    message.record_delivery_stage_at(
        "send_succeeded", base + timedelta(seconds=1.5), adapter="telegram"
    )

    durations = message.delivery_durations()

    assert "send_pacing_seconds" not in durations
    assert "send_upstream_seconds" not in durations
    assert durations["send_seconds"] == pytest.approx(1.5)


def test_pacing_disabled_records_an_explicit_zero():
    """节流**开着但这次没等**（单页回复）要如实记 0，而不是不记。

    这与上一条是两种处境：这里我们确实测了，结论是 0；上一条是没测。
    """
    message = _message()
    base = datetime.now(timezone.utc)
    message.record_delivery_stage_at("send_started", base, adapter="onebot")
    message.record_delivery_stage_at(
        "send_pacing_waited", base, adapter="onebot", pacing_seconds=0.0
    )
    message.record_delivery_stage_at(
        "send_succeeded", base + timedelta(seconds=0.4), adapter="onebot"
    )

    durations = message.delivery_durations()

    assert durations["send_pacing_seconds"] == pytest.approx(0.0)
    assert durations["send_upstream_seconds"] == pytest.approx(0.4)


def test_upstream_time_never_goes_negative():
    """节流记录若比整段还长（时钟抖动、记录顺序），上游耗时钳到 0。

    负数会让「上游有多慢」变成一个荒谬的数字，而读者无从判断是数据坏了
    还是链路真的有异常。
    """
    message = _message()
    base = datetime.now(timezone.utc)
    message.record_delivery_stage_at("send_started", base, adapter="onebot")
    message.record_delivery_stage_at(
        "send_pacing_waited",
        base + timedelta(seconds=1),
        adapter="onebot",
        pacing_seconds=5.0,
    )
    message.record_delivery_stage_at(
        "send_succeeded", base + timedelta(seconds=1), adapter="onebot"
    )

    durations = message.delivery_durations()

    assert durations["send_upstream_seconds"] == pytest.approx(0.0)


def test_a_failed_send_still_attributes_its_pacing():
    """发送失败的那次同样要分开归因。

    「等了 18 秒然后失败」和「上游 18 秒后拒了」是两个不同的故障。
    """
    message = _message()
    base = datetime.now(timezone.utc)
    message.record_delivery_stage_at("send_started", base, adapter="onebot")
    message.record_delivery_stage_at(
        "send_pacing_waited",
        base + timedelta(seconds=18),
        adapter="onebot",
        pacing_seconds=18.0,
    )
    message.record_delivery_stage_at(
        "send_failed",
        base + timedelta(seconds=19),
        adapter="onebot",
        error_type="TimeoutError",
    )

    durations = message.delivery_durations()

    assert durations["send_pacing_seconds"] == pytest.approx(18.0)
    assert durations["send_upstream_seconds"] == pytest.approx(1.0)


def test_the_pacing_stage_carries_its_seconds_in_details():
    """秒数走 details，不靠两个时间戳相减。

    节流是**一段一段**发生的（每页之前等一次），墙钟差只给出「第一次等待开始到
    最后一次等待结束」，中间还夹着真正的发送。累加值才是「我们一共主动等了多久」。
    """
    message = _message()
    base = datetime.now(timezone.utc)
    message.record_delivery_stage_at(
        "send_pacing_waited", base, adapter="onebot", pacing_seconds=7.5
    )

    event = next(
        item for item in message.delivery_timeline if item.stage == "send_pacing_waited"
    )
    assert event.details["pacing_seconds"] == pytest.approx(7.5)
