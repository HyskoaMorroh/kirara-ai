"""QQ 热更新必须是一个可观测的状态，而不是「QQ 慢」的一部分（需求 18.4、19.5）。

18.4 点名七件事各自要有诊断信息与可测试的状态转换，「QQ 热更新」是其中一件。
19.5 更明确：**不能把 LLM 慢、QQ 热更新、发送限流、WebSocket 重连、消息队列阻塞
混为一个「QQ 慢」**。

现场日志里热更新有完整时间线，而且与消息处理**重叠**：

    07:56:20.995 [QQ hotUpdate] onStatusChanged status:  DOWNLOADING
    07:56:56.423 [QQ hotUpdate] addressStatusChanged:  { status: 'complete',
    07:56:56.424 [QQ hotUpdate] onUpdateDownloaded /root/.config/QQ/versions/3.2.32-52194.zip
    07:57:10.097 [QQ hotUpdate] onStatusChanged status:  READY

`[收-私] 写一个回火算法` 恰好落在 `07:56:56` 这一刻——下载占带宽的 36 秒窗口与
一次真实对话重叠。运维事后问「那条为什么慢」时，如果面板上没有热更新这回事，
唯一能得到的结论是「QQ 慢」，而真正的原因是上游正在后台拉一个几十 MB 的包。

此前 `qr_login.py` 里 `hotUpdate` 出现次数是 **0**：日志里有，代码里没有。
文档写了「怎么关掉它」，但没有任何接口回答「它现在是不是正在跑」。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kirara_ai.im.qr_login import parse_qr_login_log

#: 现场日志的原文（时间戳格式与样本一致：`HH:MM:SS.mmm ›`，没有日期）。
DOWNLOADING = (
    "07:56:20.995 › [1a021972db7][QQ hotUpdate] onStatusChanged status:  DOWNLOADING"
)
DOWNLOADED = (
    "07:56:56.424 › [1a021972db7][QQ hotUpdate] onUpdateDownloaded "
    "/root/.config/QQ/versions/3.2.32-52194.zip"
)
READY = "07:57:10.097 › [1a021972db7][QQ hotUpdate] onStatusChanged status:  READY"
NO_UPDATE = "05:37:34.038 › [1a0211b69d2][QQ hotUpdate] [startAutoUpdate] 无需更新"
START_AUTO = (
    "05:37:32.899 › [1a0211b69d2][QQ hotUpdate] "
    "----- startAutoUpdate curVersion: 3.2.31-51102 -----"
)


def test_a_log_without_hot_update_reports_nothing():
    """没有热更新迹象时不编一个状态。

    `idle` 与「这份日志里没有热更新行」是两件事：后者可能是日志没挂全。
    """
    snapshot = parse_qr_login_log(["[I] root LLBot 8.1.8"])
    assert snapshot.hot_update is None


def test_downloading_is_visible_while_it_is_happening():
    """正在下载必须能看出来——这正是与一次慢回复重叠的那个窗口。"""
    snapshot = parse_qr_login_log([START_AUTO, DOWNLOADING])
    assert snapshot.hot_update is not None
    assert snapshot.hot_update.state == "downloading"
    # 带宽占用是「这条回复为什么慢」的一个候选原因，必须说出来。
    assert "带宽" in (snapshot.hot_update.remediation or "")


def test_the_full_transition_sequence_ends_ready():
    snapshot = parse_qr_login_log([DOWNLOADING, DOWNLOADED, READY])
    assert snapshot.hot_update.state == "ready"
    # READY 表示包已就绪、**下次重启生效**；不是「正在影响现在」。
    assert "重启" in (snapshot.hot_update.remediation or "")


def test_no_update_needed_is_distinct_from_never_checked():
    """「检查过，无需更新」与「从没检查过」处置不同。

    前者说明热更新这条线是健康的且当前不占带宽；后者可能是日志没挂全，
    也可能是 QQ 还没跑到那一步。
    """
    snapshot = parse_qr_login_log([START_AUTO, NO_UPDATE])
    assert snapshot.hot_update.state == "up_to_date"


def test_the_downloading_window_is_measurable():
    """下载起止时刻都要给出——19.5 要的是「可比链路耗时」，那需要一个区间。

    现场这段是 36 秒（07:56:20.995 → 07:56:56.424）。只说「在下载」回答不了
    「那条慢回复是不是落在这个窗口里」。
    """
    snapshot = parse_qr_login_log([DOWNLOADING, DOWNLOADED])
    hot = snapshot.hot_update
    assert hot.started_at is not None
    assert hot.completed_at is not None
    assert hot.duration_seconds == pytest.approx(35.429, abs=0.01)


def test_an_in_flight_download_has_no_completion_time():
    """还在下载时 `completed_at` 与 `duration_seconds` 必须是 None，不是 0。

    写 0 会被读成「瞬间完成」，正好与「它正在占着带宽」相反。
    """
    snapshot = parse_qr_login_log([DOWNLOADING])
    assert snapshot.hot_update.completed_at is None
    assert snapshot.hot_update.duration_seconds is None


def test_the_target_version_is_reported_without_inventing_one():
    snapshot = parse_qr_login_log([DOWNLOADING, DOWNLOADED])
    assert snapshot.hot_update.target_version == "3.2.32-52194"
    # 只有 DOWNLOADING 那行时版本号无从得知——不猜。
    assert parse_qr_login_log([DOWNLOADING]).hot_update.target_version is None


def test_hot_update_does_not_disturb_the_qr_state():
    """热更新与扫码是两条独立的线，互不覆盖。

    把热更新行折进 `state` 会让「正在下载更新」顶掉「等待扫码」，
    而操作者此刻真正需要看到的是后者。
    """
    generated = datetime(2026, 8, 20, 23, 52, 33, tzinfo=timezone.utc)
    picture = (
        "[2026-08-20T23:52:33Z INFO] listener.onQRCodeGetPicture "
        "expireTime= 120 urlLen= 68"
    )
    # 时钟必须注入：拿真实时间去比一个 2026 年的时间戳，那张码当然「已过期」，
    # 而这条用例要验的是「热更新不覆盖扫码状态」，不是过期判定。
    snapshot = parse_qr_login_log([picture, DOWNLOADING], now=lambda: generated)
    assert snapshot.state == "waiting_scan"
    assert snapshot.hot_update.state == "downloading"


def test_a_later_cycle_supersedes_an_earlier_one():
    """一份长日志里可能有多轮热更新，报告最近那一轮。

    报第一轮会让面板停在几小时前的状态上。
    """
    snapshot = parse_qr_login_log([NO_UPDATE, DOWNLOADING, DOWNLOADED, READY])
    assert snapshot.hot_update.state == "ready"


def test_the_snapshot_contains_no_account_identity():
    """与扫码快照同一条红线：诊断信息里不带账号标识。

    日志行里的 `[1a021972db7]` 是 QQ 的实例标识，不该出现在面板上。
    """
    snapshot = parse_qr_login_log([DOWNLOADING, DOWNLOADED, READY])
    dumped = snapshot.model_dump_json()
    assert "1a021972db7" not in dumped


def test_timestamps_without_a_date_are_still_usable_for_a_duration():
    """样本时间戳没有日期。

    区间长度只需要两个同源时间戳相减，因此**不需要**日期。但绝对时刻在这种日志里
    无从确定年月日，所以 `started_at` 只承诺时分秒可比，不承诺它是墙上时间——
    这一点与扫码那边「没有时间戳就报 age_unknown」是同一条纪律：
    不拿一个编出来的日期去支撑一个看起来精确的结论。
    """
    snapshot = parse_qr_login_log([DOWNLOADING, DOWNLOADED])
    assert snapshot.hot_update.duration_seconds is not None
    assert isinstance(snapshot.hot_update.started_at, datetime)
    assert snapshot.hot_update.started_at.tzinfo is timezone.utc
