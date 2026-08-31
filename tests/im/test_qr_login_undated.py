"""没有时间戳的日志行不能被当成「刚刚生成」（需求 3 的现场报障）。

现场那三行日志一个时间戳都没有：

    [PMHQ login] listener.onQRCodeGetPicture expireTime= 120 urlLen= 68
    [I] qq-protocol 二维码文件已保存: /root/llonebot/data/temp/login-qrcode.png
    [W] qq-protocol 获取登录二维码失败: QR code unavailable

`_parse_timestamp` 认两种形状（`[YYYY-MM-DDTHH:MM:SSZ` 与 `YYYY-MM-DD HH:MM:SS`），
以上三行都不匹配。`_apply` 于是回落到 `fallback_now`，也就是**调用这个函数的时刻**。

结果：`generated_at = 现在`、`remaining_seconds = 120.0`、`state = waiting_scan`
——无论那张码实际上是十分钟前生成的。面板永远显示「还剩 120 秒」，永远不会
翻成 `expired`。用户手机上说过期，面板上说有效，两边对不上。

而本模块 docstring 自己声称解决的正是这件事：「过期由本地时钟判定而非等上游日志
行」。时钟判定确实在，但它拿到的 `generated_at` 是假的，于是判定永远为真。

## 判据：**时间戳缺失是「不知道」，不是「刚刚」**

拿不到生成时刻时，唯一诚实的答复是「剩余时间未知」，而不是编一个最乐观的值。
界面上「未知」会让用户直接去取一张新码——那正是这种处境下唯一正确的动作；
「还剩 120 秒」会让他去扫一张死码，然后怀疑是自己扫错了。

## 四条边界

1. **`remaining_seconds` 为 `None`** 而不是 120：`0.0` 会被读成「刚好过期」，
   那同样是一个我们没有依据的论断。
2. **`validity_seconds` 仍然给出**：日志里 `expireTime= 120` 是真的，
   它回答「这种码能撑多久」，与「这张还剩多久」是两个问题。
3. **状态不再是 `waiting_scan`**：那个词表示「有一张可扫的码」。
   拿不到生成时刻时应该说「有一张码，但不知道是否还有效」。
4. **有时间戳时行为逐字不变**：这条修复不能动到正常路径。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kirara_ai.im.qr_login import DEFAULT_QR_VALIDITY_SECONDS, parse_qr_login_log

FIELD_LINES = [
    "[PMHQ login] listener.onLoginConnected",
    "[PMHQ login] getQRCodePicture requested (from onLoginConnected) -> true",
    "[PMHQ login] listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
    "[I] qq-protocol 二维码文件已保存: /root/llonebot/data/temp/login-qrcode.png",
]

TIMESTAMPED_LINES = [
    "[2026-08-31T00:00:00Z INFO pmhq::qr] listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
    "[2026-08-31T00:00:00Z INFO pmhq::qr] 二维码文件已保存: /root/llonebot/data/temp/login-qrcode.png",
]


def _at(*, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 8, 31, 0, minute, second, tzinfo=timezone.utc)


class TestAnUndatedQrCodeReportsUnknownRemaining:
    def test_the_remaining_time_is_not_invented(self):
        snapshot = parse_qr_login_log(FIELD_LINES)

        assert snapshot.remaining_seconds is None, (
            "拿不到生成时刻时报「还剩 120 秒」会让用户去扫一张死码"
        )

    def test_zero_is_not_used_either(self):
        """`0.0` 会被读成「刚好过期」，那同样是没有依据的论断。"""
        snapshot = parse_qr_login_log(FIELD_LINES)

        assert snapshot.remaining_seconds != 0.0

    def test_the_generated_time_is_not_invented(self):
        snapshot = parse_qr_login_log(FIELD_LINES)

        assert snapshot.generated_at is None
        assert snapshot.expires_at is None

    def test_the_declared_validity_is_still_reported(self):
        """`expireTime= 120` 是日志里的真实信息，它回答「这种码能撑多久」。"""
        snapshot = parse_qr_login_log(FIELD_LINES)

        assert snapshot.validity_seconds == DEFAULT_QR_VALIDITY_SECONDS

    def test_the_path_is_still_reported(self):
        snapshot = parse_qr_login_log(FIELD_LINES)

        assert snapshot.latest_qr_path == "/root/llonebot/data/temp/login-qrcode.png"

    def test_the_state_admits_the_age_is_unknown(self):
        """`waiting_scan` 表示「有一张可扫的码」；这里只能说「不知道还有没有效」。"""
        snapshot = parse_qr_login_log(FIELD_LINES)

        assert snapshot.state == "age_unknown"

    def test_the_remediation_tells_the_user_to_refresh(self):
        snapshot = parse_qr_login_log(FIELD_LINES)

        assert snapshot.remediation is not None
        assert "刷新" in snapshot.remediation or "新" in snapshot.remediation

    def test_it_is_not_advertised_as_scannable(self):
        """「可扫」是一个我们此刻无法保证的论断。"""
        snapshot = parse_qr_login_log(FIELD_LINES)

        assert snapshot.is_scannable is False


class TestTimestampedLogsAreUnaffected:
    def test_a_fresh_code_still_reports_its_remaining_time(self):
        snapshot = parse_qr_login_log(
            TIMESTAMPED_LINES, now=lambda: _at(minute=1, second=0)
        )

        assert snapshot.state == "waiting_scan"
        assert snapshot.remaining_seconds == 60.0

    def test_an_expired_code_is_still_reported_as_expired(self):
        snapshot = parse_qr_login_log(
            TIMESTAMPED_LINES, now=lambda: _at(minute=5, second=0)
        )

        assert snapshot.state == "expired"
        assert snapshot.failure_reason == "expired_without_scan"

    def test_a_partially_timestamped_log_uses_the_real_timestamp(self):
        """一行带时间戳就够了：`_apply` 会把它作为后续行的参照。"""
        lines = [
            "[2026-08-31T00:00:00Z INFO pmhq::qr] getQRCodePicture requested",
            "[PMHQ login] listener.onQRCodeGetPicture expireTime= 120 urlLen= 68",
        ]

        snapshot = parse_qr_login_log(lines, now=lambda: _at(minute=1, second=0))

        assert snapshot.generated_at == _at()
        assert snapshot.remaining_seconds == 60.0


class TestScannedAndSucceededDoNotNeedATimestamp:
    def test_a_scanned_code_is_not_downgraded(self):
        """已扫码之后有效期不再是用户关心的东西，缺时间戳不该改变状态。"""
        lines = FIELD_LINES + [
            "[PMHQ login] listener.onQRCodeSessionUserScaned",
        ]

        assert parse_qr_login_log(lines).state == "scanned"

    def test_a_successful_login_is_not_downgraded(self):
        lines = FIELD_LINES + [
            '[PMHQ login] listener.onQRCodeLoginSucceed {"account":"1"}',
        ]

        assert parse_qr_login_log(lines).state == "succeeded"


class TestTheStateIsDeclaredInTheSchema:
    def test_the_new_state_is_part_of_the_literal(self):
        """消费方按字面量分支；值不在类型里，这个状态就出不了这个模块。"""
        from kirara_ai.im.qr_login import QRLoginSnapshot

        annotation = QRLoginSnapshot.model_fields["state"].annotation
        assert "age_unknown" in getattr(annotation, "__args__", ())
