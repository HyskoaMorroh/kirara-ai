"""QR login lifecycle observed from the OneBot implementation's own log.

Kirara does not generate the QR code — LLOneBot / PMHQ does, inside its own
container. What Kirara *can* do is read the log that container already writes to
a mounted volume and fold it into one answerable state, because the questions an
operator actually asks are not answerable from a scrollback:

- Is the code on screen still valid, or am I looking at an expired one?
- When was it generated, and how long do I have left?
- Did someone already scan it and just not confirm?
- If there is no code, *why* — service not ready yet, or a real failure?

The alternative to this module is what existed before: an operator greps the log
by hand and guesses which `onQRCodeGetPicture` line is the newest. That guess is
exactly the reported failure mode ("二维码总是过期"): the code shown in a stale
terminal buffer is expired, while a fresh one already exists on disk.

Design constraints:

- **Read-only.** This module never asks the upstream to refresh. Refresh is
  LLOneBot's own behaviour (it re-requests a picture when one expires); modelling
  it here as an action we take would be a lie about who owns the lifecycle.
- **No account identifiers.** Log lines carry uin, uid, nickname and avatar
  URLs. None of that reaches the snapshot: a login-state panel must not become a
  place where account identity leaks.
- **Expiry is computed, never stored as a state.** A snapshot taken 130 seconds
  after generation reports ``expired`` even though no log line said so, because
  the QQ server invalidates on its own clock. Waiting for an upstream line would
  keep showing a dead code as valid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Literal, Optional

from pydantic import BaseModel, Field


#: 二维码生命周期状态。
#:
#: ``unavailable`` 与 ``failed`` 必须分开：前者是 QQ 服务尚未就绪时的启动期噪声
#: （随后就会拿到真正的二维码），后者是登录本身失败。把两者混成一个「出错」，
#: 操作者会在正常启动过程中白等或白重启。
QRLoginState = Literal[
    "unknown",        # 还没有任何可解读的事件
    "pending",        # 已请求，但还没拿到图
    "waiting_scan",   # 有有效二维码，等待扫码
    "scanned",        # 已扫码，等待手机端确认
    "expired",        # 超过有效期且未登录成功
    "succeeded",      # 登录成功
    "failed",         # 明确失败
    "unavailable",    # 上游暂时给不出二维码（通常是启动期）
    "quick_login",    # 走了免扫码快速登录，本轮不会有二维码
]

#: 失败原因码。稳定、脱敏、可用于外部监控；绝不回传上游原始消息。
QRFailureReason = Literal[
    "qr_code_unavailable",
    "no_saved_credential",
    "login_failed",
    "expired_without_scan",
]

#: LLOneBot 未给出 ``expireTime`` 时采用的有效期。
#:
#: 实测日志里该值一直是 120 秒（``onQRCodeGetPicture expireTime= 120``）。
#: 这里只作为兜底：日志给了就用日志的值，不猜。
DEFAULT_QR_VALIDITY_SECONDS = 120


class QRLoginSnapshot(BaseModel):
    """One answerable view of the QR login lifecycle. Contains no account data."""

    state: QRLoginState = "unknown"
    #: 二维码生成时刻（首次拿到图的日志时间）。
    generated_at: Optional[datetime] = None
    #: 失效时刻 = 生成时刻 + 有效期。
    expires_at: Optional[datetime] = None
    #: 上游声明的有效期秒数。
    validity_seconds: Optional[int] = Field(default=None, ge=0)
    #: 距失效还剩多少秒；已失效为 0，无二维码为 None。
    remaining_seconds: Optional[float] = Field(default=None, ge=0)
    #: 最新二维码文件路径（容器内路径，原样取自日志）。
    latest_qr_path: Optional[str] = None
    #: 本轮观察到的二维码刷新次数（第一张不计为刷新）。
    refresh_count: int = Field(default=0, ge=0)
    #: 稳定失败原因码，不含上游原文。
    failure_reason: Optional[QRFailureReason] = None
    #: 最后一条被解读的事件时间。
    last_event_at: Optional[datetime] = None
    #: 供界面直接展示的一句处置建议。
    remediation: Optional[str] = None

    @property
    def is_scannable(self) -> bool:
        """Whether the code this snapshot describes can still be scanned."""
        return self.state in {"waiting_scan", "scanned"}


@dataclass
class _Accumulator:
    """Mutable fold state while replaying log lines."""

    state: QRLoginState = "unknown"
    generated_at: Optional[datetime] = None
    validity_seconds: Optional[int] = None
    latest_qr_path: Optional[str] = None
    picture_count: int = 0
    failure_reason: Optional[QRFailureReason] = None
    last_event_at: Optional[datetime] = None


# 日志行形态取自实际 LLOneBot / PMHQ 输出，见本文件 docstring 引用的样本。
_TIMESTAMP_PATTERNS = (
    # [2026-08-20T21:37:19Z INFO  pmhq::qr_channel] ...
    re.compile(r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z"),
    # 2026-08-20 21:37:19 ...
    re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"),
)
_GET_PICTURE = re.compile(
    r"onQRCodeGetPicture\s+expireTime=\s*(\d+)", re.IGNORECASE
)
_PICTURE_REQUESTED = re.compile(r"getQRCodePicture requested", re.IGNORECASE)
_PICTURE_REFRESH = re.compile(r"qr pipe refresh\s*->\s*getQRCodePicture", re.IGNORECASE)
_QR_SAVED = re.compile(r"二维码文件已保存[:：]\s*(\S+)")
_QR_SAVED_EN = re.compile(r"qr ?code (?:file )?saved(?: to)?[:：]\s*(\S+)", re.IGNORECASE)
_SCANNED = re.compile(r"onQRCodeSessionUserScaned", re.IGNORECASE)
_SUCCEEDED = re.compile(r"onQRCodeLoginSucceed", re.IGNORECASE)
_LOGIN_FAILED = re.compile(r"onLoginFailed", re.IGNORECASE)
_UNAVAILABLE = re.compile(r"QR code unavailable", re.IGNORECASE)
_NO_CREDENTIAL = re.compile(r"uin not in saved-credential list", re.IGNORECASE)
_QUICK_LOGIN = re.compile(r"quickLoginWithUin|quick login entry ready", re.IGNORECASE)

_REMEDIATION: dict[QRLoginState, str] = {
    "unknown": "尚未观察到登录事件；确认已挂载 OneBot 实现的日志目录。",
    "pending": "已请求二维码，等待上游生成；通常几秒内完成。",
    "waiting_scan": "用最新路径下的二维码扫码；不要扫终端里往上翻出的旧图。",
    "scanned": "已扫码，请在手机 QQ 上确认登录。",
    "expired": "二维码已过期。上游会自动生成新的，请取最新路径重新扫码。",
    "succeeded": "登录成功，登录态已写入挂载目录，后续重启免扫码。",
    "failed": "登录失败，检查上游日志中的错误码与账号状态。",
    "unavailable": "上游暂时给不出二维码。启动期出现属正常，持续出现才需排查。",
    "quick_login": "本轮走免扫码快速登录，不会生成二维码。",
}


def _parse_timestamp(line: str) -> Optional[datetime]:
    for pattern in _TIMESTAMP_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        raw = match.group(1).replace(" ", "T")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            continue
        # 日志里的 `Z` 后缀是 UTC；没有时区标记时也按 UTC 处理，
        # 因为容器默认跑在 UTC，而带时区的时间戳是相减求耗时的前提。
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    return None


def _apply(accumulator: _Accumulator, line: str, fallback_now: datetime) -> None:
    """Fold one log line into the accumulator."""
    timestamp = _parse_timestamp(line) or accumulator.last_event_at or fallback_now

    picture = _GET_PICTURE.search(line)
    if picture is not None:
        accumulator.picture_count += 1
        accumulator.generated_at = timestamp
        accumulator.validity_seconds = int(picture.group(1))
        accumulator.state = "waiting_scan"
        # 新二维码作废此前的失败原因：上游已经给出了可扫的新图。
        accumulator.failure_reason = None
        accumulator.last_event_at = timestamp
        return

    saved = _QR_SAVED.search(line) or _QR_SAVED_EN.search(line)
    if saved is not None:
        accumulator.latest_qr_path = saved.group(1)
        accumulator.last_event_at = timestamp
        if accumulator.state in {"unknown", "pending", "unavailable"}:
            # 有文件就说明有可扫的图，即使 expireTime 那行没被采到。
            accumulator.state = "waiting_scan"
            accumulator.generated_at = accumulator.generated_at or timestamp
        return

    if _SUCCEEDED.search(line):
        accumulator.state = "succeeded"
        accumulator.failure_reason = None
        accumulator.last_event_at = timestamp
        return

    if _SCANNED.search(line):
        accumulator.state = "scanned"
        accumulator.last_event_at = timestamp
        return

    if _LOGIN_FAILED.search(line):
        accumulator.state = "failed"
        accumulator.failure_reason = "login_failed"
        accumulator.last_event_at = timestamp
        return

    if _UNAVAILABLE.search(line):
        # 只在还没有可扫二维码时才降级；已经拿到图之后的这行是历史噪声。
        if accumulator.state in {"unknown", "pending"}:
            accumulator.state = "unavailable"
            accumulator.failure_reason = "qr_code_unavailable"
        accumulator.last_event_at = timestamp
        return

    if _NO_CREDENTIAL.search(line):
        # 这不是失败：它恰好说明「只能扫码」，二维码仍然可用。
        if accumulator.state in {"unknown", "pending", "unavailable"}:
            accumulator.state = "pending"
        accumulator.failure_reason = "no_saved_credential"
        accumulator.last_event_at = timestamp
        return

    if _QUICK_LOGIN.search(line):
        if accumulator.state in {"unknown", "pending", "unavailable"}:
            accumulator.state = "quick_login"
        accumulator.last_event_at = timestamp
        return

    if _PICTURE_REFRESH.search(line) or _PICTURE_REQUESTED.search(line):
        if accumulator.state in {"unknown", "unavailable"}:
            accumulator.state = "pending"
        accumulator.last_event_at = timestamp
        return


def parse_qr_login_log(
    lines: Iterable[str],
    *,
    now: Optional[Callable[[], datetime]] = None,
) -> QRLoginSnapshot:
    """Fold OneBot-implementation log lines into one QR login snapshot.

    时钟通过 ``now`` 注入，因此「过期判定」可被测试确定性地覆盖，而不需要
    真的等 120 秒。
    """
    clock = now or (lambda: datetime.now(timezone.utc))
    reference = clock()
    accumulator = _Accumulator()
    for line in lines:
        if line:
            _apply(accumulator, line, reference)

    generated_at = accumulator.generated_at
    validity = accumulator.validity_seconds
    if generated_at is not None and validity is None:
        validity = DEFAULT_QR_VALIDITY_SECONDS
    expires_at = (
        generated_at + timedelta(seconds=validity)
        if generated_at is not None and validity is not None
        else None
    )

    state = accumulator.state
    failure_reason = accumulator.failure_reason
    remaining: Optional[float] = None
    if expires_at is not None:
        remaining = max(0.0, (expires_at - reference).total_seconds())
        # 过期由时钟判定，不等上游那行日志：等它就会一直把死码显示成有效。
        # 已扫码或已成功不受影响——扫码之后有效期不再是用户关心的东西。
        if remaining == 0.0 and state == "waiting_scan":
            state = "expired"
            failure_reason = "expired_without_scan"

    return QRLoginSnapshot(
        state=state,
        generated_at=generated_at,
        expires_at=expires_at,
        validity_seconds=validity,
        remaining_seconds=remaining,
        latest_qr_path=accumulator.latest_qr_path,
        refresh_count=max(0, accumulator.picture_count - 1),
        failure_reason=failure_reason,
        last_event_at=accumulator.last_event_at,
        remediation=_REMEDIATION.get(state),
    )
