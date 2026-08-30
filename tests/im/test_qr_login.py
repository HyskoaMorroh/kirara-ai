"""QR 登录生命周期解析：把上游日志折叠成一个可回答的状态。

为什么需要它：二维码由 LLOneBot / PMHQ 在自己的容器里生成，Kirara 不参与。
但「我屏幕上这张还能扫吗」「已经扫了为什么没进去」「没有码是因为服务没起来还是真失败」
这三个问题，靠人工翻 scrollback 是答不出来的——而「二维码总是过期」这个报障，
根因恰恰就是操作者扫了终端缓冲区里往上翻出来的旧图。

这些用例覆盖三件事：状态机的每次转移、过期由时钟判定（不等上游日志）、
以及快照里绝不出现账号标识。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kirara_ai.im.qr_login import (
    DEFAULT_QR_VALIDITY_SECONDS,
    QRLoginSnapshot,
    parse_qr_login_log,
)


BASE = datetime(2026, 8, 20, 21, 37, 19, tzinfo=timezone.utc)


def at(offset_seconds: int) -> str:
    """A log timestamp prefix in the exact shape PMHQ emits."""
    stamp = (BASE + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%dT%H:%M:%S")
    return f"[{stamp}Z INFO  pmhq::qr_channel]"


def clock_at(offset_seconds: int):
    return lambda: BASE + timedelta(seconds=offset_seconds)


def test_no_events_reports_unknown_rather_than_guessing():
    snapshot = parse_qr_login_log([], now=clock_at(0))

    assert snapshot.state == "unknown"
    assert snapshot.generated_at is None
    assert snapshot.expires_at is None
    assert snapshot.remaining_seconds is None
    assert snapshot.remediation


def test_a_generated_picture_yields_validity_and_remaining_time():
    """有效期、生成时间、剩余秒数三项必须同时给出。"""
    lines = [
        f"{at(0)} [PMHQ login] listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
        f"{at(1)} [I] qq-protocol 二维码文件已保存: /root/llonebot/data/temp/login-qrcode.png",
    ]

    snapshot = parse_qr_login_log(lines, now=clock_at(30))

    assert snapshot.state == "waiting_scan"
    assert snapshot.is_scannable is True
    assert snapshot.validity_seconds == 120
    assert snapshot.generated_at == BASE
    assert snapshot.expires_at == BASE + timedelta(seconds=120)
    assert snapshot.remaining_seconds == pytest.approx(90.0)
    assert snapshot.latest_qr_path == "/root/llonebot/data/temp/login-qrcode.png"
    assert snapshot.failure_reason is None


def test_expiry_is_decided_by_the_clock_not_by_an_upstream_line():
    """过期必须由时钟判定。

    等上游打一行「已过期」才改状态，就会在这段等待里一直把死码显示成有效——
    而这正是「二维码总是过期」这个报障的根因：面板说有效，手机说过期。
    """
    lines = [f"{at(0)} listener.onQRCodeGetPicture expireTime= 120 urlLen= 68"]

    still_valid = parse_qr_login_log(lines, now=clock_at(119))
    just_expired = parse_qr_login_log(lines, now=clock_at(121))

    assert still_valid.state == "waiting_scan"
    assert just_expired.state == "expired"
    assert just_expired.is_scannable is False
    assert just_expired.remaining_seconds == 0
    assert just_expired.failure_reason == "expired_without_scan"


def test_a_refreshed_picture_supersedes_the_expired_one():
    """上游自动刷新后，快照必须描述**新**码，并计入刷新次数。"""
    lines = [
        f"{at(0)} listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
        f"{at(125)} [PMHQ login] qr pipe refresh -> getQRCodePicture -> true",
        f"{at(126)} listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
        f"{at(127)} [I] qq-protocol 二维码文件已保存: /root/llonebot/data/temp/login-qrcode.png",
    ]

    snapshot = parse_qr_login_log(lines, now=clock_at(130))

    assert snapshot.state == "waiting_scan"
    assert snapshot.generated_at == BASE + timedelta(seconds=126)
    assert snapshot.remaining_seconds == pytest.approx(116.0)
    assert snapshot.refresh_count == 1


def test_scanned_but_unconfirmed_is_its_own_state():
    """已扫码未确认与等待扫码必须区分：前者该催手机，后者该去扫。"""
    lines = [
        f"{at(0)} listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
        f"{at(10)} [PMHQ login] listener.onQRCodeSessionUserScaned 0 http://qh.qlogo.cn/g?b=qq",
    ]

    snapshot = parse_qr_login_log(lines, now=clock_at(15))

    assert snapshot.state == "scanned"
    assert snapshot.is_scannable is True
    assert "确认" in (snapshot.remediation or "")


def test_scanned_state_survives_the_validity_window():
    """扫码之后有效期不再是用户关心的东西，不能被改回 expired。"""
    lines = [
        f"{at(0)} listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
        f"{at(10)} listener.onQRCodeSessionUserScaned 0 http://qh.qlogo.cn/g?b=qq",
    ]

    snapshot = parse_qr_login_log(lines, now=clock_at(200))

    assert snapshot.state == "scanned"


def test_successful_login_is_terminal_and_clears_failures():
    lines = [
        f"{at(0)} [W] qq-protocol 获取登录二维码失败: QR code unavailable",
        f"{at(5)} listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
        f"{at(20)} [PMHQ login] listener.onQRCodeLoginSucceed {{\"account\":\"1234567890\"}}",
    ]

    snapshot = parse_qr_login_log(lines, now=clock_at(400))

    assert snapshot.state == "succeeded"
    assert snapshot.failure_reason is None
    # 即使早已超过有效期，成功也不能被改写成过期。
    assert snapshot.is_scannable is False


def test_startup_noise_is_not_reported_as_a_failure_state():
    """`QR code unavailable` 在启动期是正常噪声，与登录失败必须分开。

    把两者混成一个「出错」，操作者会在正常启动过程里白等或白重启。
    """
    only_noise = parse_qr_login_log(
        [f"{at(0)} [W] qq-protocol 获取登录二维码失败: QR code unavailable"],
        now=clock_at(1),
    )

    assert only_noise.state == "unavailable"
    assert only_noise.failure_reason == "qr_code_unavailable"
    assert only_noise.state != "failed"
    assert "启动期" in (only_noise.remediation or "")


def test_a_later_picture_clears_the_unavailable_noise():
    lines = [
        f"{at(0)} [W] qq-protocol 获取登录二维码失败: QR code unavailable",
        f"{at(3)} listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
    ]

    snapshot = parse_qr_login_log(lines, now=clock_at(5))

    assert snapshot.state == "waiting_scan"
    assert snapshot.failure_reason is None


def test_login_failure_is_reported_with_a_stable_reason_code():
    lines = [
        f"{at(0)} listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
        f"{at(30)} [PMHQ login] listener.onLoginFailed 3 device lock",
    ]

    snapshot = parse_qr_login_log(lines, now=clock_at(31))

    assert snapshot.state == "failed"
    assert snapshot.failure_reason == "login_failed"


def test_missing_saved_credential_means_scan_required_not_failed():
    """`uin not in saved-credential list` 说明「只能扫码」，二维码仍然可用。"""
    snapshot = parse_qr_login_log(
        [f"{at(0)} [PMHQ login] uin not in saved-credential list; QR login remains available 1234567890 []"],
        now=clock_at(1),
    )

    assert snapshot.state == "pending"
    assert snapshot.failure_reason == "no_saved_credential"


def test_quick_login_reports_that_no_qr_will_appear():
    """免扫码快速登录时不该让操作者等一张永远不会出现的二维码。"""
    snapshot = parse_qr_login_log(
        [f"{at(0)} [PMHQ login] diag: quick login entry ready; delaying quickLoginWithUin 1000 ms"],
        now=clock_at(1),
    )

    assert snapshot.state == "quick_login"
    assert snapshot.is_scannable is False
    assert "免扫码" in (snapshot.remediation or "")


def test_validity_falls_back_when_the_upstream_omits_expire_time():
    """只看到「文件已保存」时仍要给出有效期，用实测的 120 秒兜底。"""
    snapshot = parse_qr_login_log(
        [f"{at(0)} [I] qq-protocol 二维码文件已保存: /root/llonebot/data/temp/login-qrcode.png"],
        now=clock_at(10),
    )

    assert snapshot.state == "waiting_scan"
    assert snapshot.validity_seconds == DEFAULT_QR_VALIDITY_SECONDS
    assert snapshot.expires_at == BASE + timedelta(
        seconds=DEFAULT_QR_VALIDITY_SECONDS
    )


def test_the_snapshot_never_carries_account_identifiers():
    """日志里有 uin、uid、昵称与头像 URL；快照里一个都不能出现。

    登录状态面板不能变成账号身份泄露的地方。

    夹具用的是**合成**账号标识，不是真实日志里的那一组：把真实 uin / uid 写进
    测试，本身就违反「不得把私有数据写入源码与测试」这条约束——即使这条测试
    断言的正是「不泄露」。合成值同样能证明脱敏生效。
    """
    lines = [
        f"{at(0)} listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
        f"{at(5)} [PMHQ login] diag: matched login entry "
        '{"uin":"1000000001","uid":"u_SyntheticUidForTestOnly","nickName":"测试账号",'
        '"faceUrl":"https://example.invalid/avatar?k=synthetic"}',
        f"{at(20)} [PMHQ login] listener.onQRCodeLoginSucceed "
        '{"account":"1000000001","uid":"u_SyntheticUidForTestOnly"}',
    ]

    serialized = parse_qr_login_log(lines, now=clock_at(25)).model_dump_json()

    for leaked in (
        "1000000001",
        "u_SyntheticUidForTestOnly",
        "测试账号",
        "example.invalid",
    ):
        assert leaked not in serialized


def test_timestamps_are_timezone_aware():
    """时间戳必须带时区：两个时刻相减是剩余时间的唯一依据。"""
    snapshot = parse_qr_login_log(
        [f"{at(0)} listener.onQRCodeGetPicture expireTime= 120 urlLen= 68"],
        now=clock_at(1),
    )

    assert snapshot.generated_at is not None
    assert snapshot.generated_at.tzinfo is not None
    assert snapshot.expires_at is not None
    assert snapshot.expires_at.tzinfo is not None


def test_unparsable_lines_are_ignored_without_raising():
    """日志里混入乱码或半行不得让解析抛错——观测不能成为新的失败点。"""
    lines = [
        "",
        "not a log line at all",
        "[bad-timestamp] listener.onQRCodeGetPicture expireTime= 120",
        f"{at(0)} listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
    ]

    snapshot = parse_qr_login_log(lines, now=clock_at(2))

    assert snapshot.state == "waiting_scan"


def test_snapshot_is_serializable_for_an_api_response():
    snapshot = parse_qr_login_log(
        [f"{at(0)} listener.onQRCodeGetPicture expireTime= 120 urlLen= 68"],
        now=clock_at(10),
    )

    payload = snapshot.model_dump(mode="json")

    assert payload["state"] == "waiting_scan"
    assert isinstance(payload["generated_at"], str)
    assert isinstance(payload["remaining_seconds"], (int, float))
    assert isinstance(QRLoginSnapshot.model_validate(payload), QRLoginSnapshot)
