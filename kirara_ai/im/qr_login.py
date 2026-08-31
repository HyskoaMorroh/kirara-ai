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
- **A missing timestamp is "unknown", never "just now".** Some PMHQ builds write
  the QR lines without any date at all (``[PMHQ login] listener.onQRCodeGetPicture
  expireTime= 120``). Falling back to the read time would make every such code
  report a full 120 seconds remaining forever — the computed-expiry guarantee
  above would hold on paper while resting on an invented generation time, and the
  panel would keep advertising a dead code as fresh. Those logs report
  ``age_unknown`` with no remaining time instead, which points the operator at
  the one action that is actually correct there: get a new code.
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
    "age_unknown",    # 有二维码，但日志没给时间戳，无法判断它是否还有效
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

#: QQ 自身热更新的状态（需求 18.4 点名的七件事之一）。
#:
#: 与扫码状态是**两条独立的线**：热更新在后台拉一个几十 MB 的包，占带宽、
#: 可能拖慢登录与消息投递，但它不影响「这张二维码还能不能扫」。
#: 折进同一个 ``state`` 会让「正在下载更新」顶掉「等待扫码」，
#: 而操作者此刻真正需要看到的是后者。
HotUpdateState = Literal[
    "checking",     # 已开始检查（startAutoUpdate），还没有结论
    "up_to_date",   # 检查过，无需更新——与「从没检查过」处置不同
    "downloading",  # 正在下载：占带宽，是「这条回复为什么慢」的候选原因
    "downloaded",   # 包已下载完，尚未就绪
    "ready",        # 包已就绪，下次重启生效；此刻不再占带宽
    "failed",       # 明确失败
]


class HotUpdateSnapshot(BaseModel):
    """One answerable view of QQ's own hot-update cycle. Contains no account data."""

    state: HotUpdateState
    #: 目标版本号；日志没给出时为 ``None``（不猜）。
    target_version: Optional[str] = None
    #: 本轮开始时刻。
    #:
    #: 这类日志行只有 ``HH:MM:SS.mmm``，没有日期，因此它只承诺**时分秒可比**，
    #: 不承诺是墙上时间。与扫码那边「没有时间戳就报 age_unknown」同一条纪律：
    #: 不拿一个编出来的日期去支撑一个看起来精确的结论。
    started_at: Optional[datetime] = None
    #: 下载完成时刻；还在下载时为 ``None``。
    completed_at: Optional[datetime] = None
    #: 下载窗口长度（秒）。**还在进行时为 ``None`` 而不是 0**——
    #: 0 会被读成「瞬间完成」，正好与「它正在占着带宽」相反。
    duration_seconds: Optional[float] = Field(default=None, ge=0)
    #: 供界面直接展示的一句处置建议。
    remediation: Optional[str] = None


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
    #: QQ 自身热更新的状态；日志里没有任何热更新行时为 ``None``。
    #:
    #: ``None`` 与 ``up_to_date`` 是两件事：后者是「检查过，无需更新」，
    #: 前者可能只是日志没挂全。19.5 要求热更新不能与「QQ 慢」混成一件事，
    #: 而混淆的第一步就是让它在面板上根本不存在。
    hot_update: Optional[HotUpdateSnapshot] = None

    @property
    def is_scannable(self) -> bool:
        """Whether the code this snapshot describes can still be scanned.

        ``age_unknown`` 不算：那是「有一张码但不知道它是否还有效」，
        说它可扫是一个此刻无法保证的论断。
        """
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
    #: 拿到二维码的那几行日志里有没有真实时间戳。
    #:
    #: 为假时 ``generated_at`` 只能是「读日志的时刻」，据它算出的剩余时间必然是
    #: 满额——面板会永远显示「还剩 120 秒」，而那张码可能十分钟前就死了。
    generated_at_is_real: bool = False
    #: 热更新本轮的状态；``None`` 表示日志里没有任何热更新行。
    hot_update_state: Optional[HotUpdateState] = None
    hot_update_version: Optional[str] = None
    hot_update_started_at: Optional[datetime] = None
    hot_update_completed_at: Optional[datetime] = None

    #: ``last_event_at`` 本身是不是真实时间戳。
    #:
    #: 与 ``generated_at_is_real`` 分开：一段日志可能前几行带时间戳、
    #: 拿到图那行不带（PMHQ 的 `[PMHQ login]` 前缀就没有日期）。这种情况下
    #: 继承前一条真实事件的时间戳是合理的，误差只有几毫秒。
    last_event_at_is_real: bool = False


def _note_event(
    accumulator: _Accumulator, timestamp: datetime, is_real: bool
) -> None:
    """Record one observed event's time together with whether it was real.

    两者必须一起写：分开写迟早出现「时间戳是编的、标记说是真的」，
    而那种不一致的症状正是这个模块要消灭的——面板给出一个没有依据的剩余秒数。
    """
    accumulator.last_event_at = timestamp
    accumulator.last_event_at_is_real = is_real


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

#: 热更新日志行。取自实际输出，见本文件 docstring 引用的样本。
#:
#: 这类行的时间戳只有 `HH:MM:SS.mmm ›`（没有日期），因此单独一个模式；
#: 复用 `_TIMESTAMP_PATTERNS` 匹配不到它们。
_CLOCK_ONLY = re.compile(r"^\s*(\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)\s*›")
_HOT_UPDATE_LINE = re.compile(r"\[QQ hotUpdate\]", re.IGNORECASE)
_HOT_UPDATE_START = re.compile(r"startAutoUpdate curVersion", re.IGNORECASE)
_HOT_UPDATE_NONE = re.compile(r"\[startAutoUpdate\]\s*无需更新|no update needed", re.IGNORECASE)
_HOT_UPDATE_STATUS = re.compile(r"onStatusChanged status:\s*(\w+)", re.IGNORECASE)
_HOT_UPDATE_DOWNLOADED = re.compile(
    r"onUpdateDownloaded\s+(\S+)", re.IGNORECASE
)
_HOT_UPDATE_PACKAGE = re.compile(r"onPackageReady packageDirPath:\s*(\S+)", re.IGNORECASE)
_HOT_UPDATE_FAILED = re.compile(r"onUpdateFailed|updateError|下载失败", re.IGNORECASE)
#: 版本号形态：`3.2.32-52194`，可能出现在 zip 路径或 check 行里。
_HOT_UPDATE_VERSION = re.compile(r"(\d+\.\d+\.\d+-\d+)")

_HOT_UPDATE_REMEDIATION: dict[HotUpdateState, str] = {
    "checking": "QQ 正在检查自身更新，通常几秒内结束，不影响收发消息。",
    "up_to_date": "QQ 已是最新版本，本轮热更新不占带宽。",
    "downloading": (
        "QQ 正在后台下载自身更新包（通常几十 MB），会占用带宽并可能拖慢这段时间内的"
        "登录与消息投递。这与 Kirara 无关；落在这个窗口里的慢回复应先排除它，"
        "再去查模型或发送链路。要彻底关掉见 QQ_ONEBOT_OPERATIONS.md「关闭 QQ 热更新」。"
    ),
    "downloaded": "更新包已下载完，正在准备；带宽占用已结束。",
    "ready": "更新包已就绪，将在 QQ 下次重启时生效；此刻不再占用带宽。",
    "failed": "QQ 热更新失败。它不影响当前登录态，但会反复重试并占带宽。",
}

_REMEDIATION: dict[QRLoginState, str] = {
    "unknown": "尚未观察到登录事件；确认已挂载 OneBot 实现的日志目录。",
    "pending": "已请求二维码，等待上游生成；通常几秒内完成。",
    "waiting_scan": "用最新路径下的二维码扫码；不要扫终端里往上翻出的旧图。",
    "age_unknown": (
        "上游日志没有时间戳，无法判断这张二维码是否还有效（有效期只有 120 秒）。"
        "先点「刷新扫码状态」取最新一张再扫，不要扫屏幕上已有的那张。"
    ),
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


def _parse_clock_only(line: str) -> Optional[datetime]:
    """把只有 ``HH:MM:SS.mmm`` 的时间戳解析成一个**可相减**的时刻。

    热更新日志行没有日期。区间长度只需要两个同源时间戳相减，因此不需要日期；
    这里固定挂在 1970-01-01 上并标记 UTC——它承诺的只是「时分秒可比」，
    不承诺是墙上时间。与扫码那边「没有时间戳就报 age_unknown」同一条纪律：
    不拿一个编出来的日期去支撑一个看起来精确的结论。
    """
    match = _CLOCK_ONLY.match(line)
    if match is None:
        return None
    raw = match.group(1)
    try:
        return datetime.fromisoformat(f"1970-01-01T{raw}").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _apply_hot_update(accumulator: _Accumulator, line: str) -> None:
    """Fold one ``[QQ hotUpdate]`` line into the accumulator.

    与扫码状态完全分开：热更新占带宽、可能拖慢投递，但不影响「这张码还能不能扫」。
    19.5 要求两者不能混成一个「QQ 慢」，而混淆的第一步就是共用一个状态字段。
    """
    timestamp = _parse_clock_only(line)
    version = _HOT_UPDATE_VERSION.search(line)

    if _HOT_UPDATE_FAILED.search(line):
        accumulator.hot_update_state = "failed"
        return

    status = _HOT_UPDATE_STATUS.search(line)
    if status is not None:
        value = status.group(1).strip().lower()
        if value == "downloading":
            accumulator.hot_update_state = "downloading"
            # 每一轮下载重新起算：一份长日志里可能有多轮，
            # 报第一轮会让面板停在几小时前的状态上。
            accumulator.hot_update_started_at = timestamp
            accumulator.hot_update_completed_at = None
            return
        if value == "ready":
            accumulator.hot_update_state = "ready"
            return
        if value == "idle":
            # `getUpdateStatus: IDLE` 只是在报「当前没有下载在跑」，
            # 它不构成「检查过且无需更新」这个结论。
            return

    downloaded = _HOT_UPDATE_DOWNLOADED.search(line) or _HOT_UPDATE_PACKAGE.search(line)
    if downloaded is not None:
        # 已 ready 的不要被随后出现的 packageDirPath 行拉回 downloaded：
        # 状态只能前进。
        if accumulator.hot_update_state != "ready":
            accumulator.hot_update_state = "downloaded"
        if accumulator.hot_update_completed_at is None:
            accumulator.hot_update_completed_at = timestamp
        if version is not None:
            accumulator.hot_update_version = version.group(1)
        return

    if _HOT_UPDATE_NONE.search(line):
        accumulator.hot_update_state = "up_to_date"
        return

    if _HOT_UPDATE_START.search(line):
        # 一轮新的检查开始：清掉上一轮的时刻，否则两轮会被算成一个跨越几小时的
        # 「下载窗口」。
        accumulator.hot_update_state = "checking"
        accumulator.hot_update_started_at = timestamp
        accumulator.hot_update_completed_at = None
        accumulator.hot_update_version = None
        return

    # 其余 hotUpdate 行（clearOldVersions、dnsDefaultOrder 之类）只用于确认
    # 「这条线存在」，不改变状态；但版本号若在其中出现则记下来。
    if version is not None and accumulator.hot_update_version is None:
        accumulator.hot_update_version = version.group(1)
    if accumulator.hot_update_state is None:
        accumulator.hot_update_state = "checking"


def _apply(accumulator: _Accumulator, line: str, fallback_now: datetime) -> None:
    """Fold one log line into the accumulator."""
    # 热更新是**另一条线**：先分流，不让它进扫码状态机。
    if _HOT_UPDATE_LINE.search(line):
        _apply_hot_update(accumulator, line)
        return

    parsed = _parse_timestamp(line)
    timestamp = parsed or accumulator.last_event_at or fallback_now
    # 这一行**自己**带时间戳，还是继承了前一条真实事件的时间戳？两者都算真实；
    # 只有回落到 `fallback_now`（读日志的时刻）才不算。
    timestamp_is_real = parsed is not None or accumulator.last_event_at_is_real

    picture = _GET_PICTURE.search(line)
    if picture is not None:
        accumulator.picture_count += 1
        accumulator.generated_at = timestamp
        accumulator.generated_at_is_real = timestamp_is_real
        accumulator.validity_seconds = int(picture.group(1))
        accumulator.state = "waiting_scan"
        # 新二维码作废此前的失败原因：上游已经给出了可扫的新图。
        accumulator.failure_reason = None
        _note_event(accumulator, timestamp, timestamp_is_real)
        return

    saved = _QR_SAVED.search(line) or _QR_SAVED_EN.search(line)
    if saved is not None:
        accumulator.latest_qr_path = saved.group(1)
        _note_event(accumulator, timestamp, timestamp_is_real)
        if accumulator.state in {"unknown", "pending", "unavailable"}:
            # 有文件就说明有可扫的图，即使 expireTime 那行没被采到。
            accumulator.state = "waiting_scan"
            if accumulator.generated_at is None:
                accumulator.generated_at = timestamp
                accumulator.generated_at_is_real = timestamp_is_real
        return

    if _SUCCEEDED.search(line):
        accumulator.state = "succeeded"
        accumulator.failure_reason = None
        _note_event(accumulator, timestamp, timestamp_is_real)
        return

    if _SCANNED.search(line):
        accumulator.state = "scanned"
        _note_event(accumulator, timestamp, timestamp_is_real)
        return

    if _LOGIN_FAILED.search(line):
        accumulator.state = "failed"
        accumulator.failure_reason = "login_failed"
        _note_event(accumulator, timestamp, timestamp_is_real)
        return

    if _UNAVAILABLE.search(line):
        # 只在还没有可扫二维码时才降级；已经拿到图之后的这行是历史噪声。
        if accumulator.state in {"unknown", "pending"}:
            accumulator.state = "unavailable"
            accumulator.failure_reason = "qr_code_unavailable"
        _note_event(accumulator, timestamp, timestamp_is_real)
        return

    if _NO_CREDENTIAL.search(line):
        # 这不是失败：它恰好说明「只能扫码」，二维码仍然可用。
        if accumulator.state in {"unknown", "pending", "unavailable"}:
            accumulator.state = "pending"
        accumulator.failure_reason = "no_saved_credential"
        _note_event(accumulator, timestamp, timestamp_is_real)
        return

    if _QUICK_LOGIN.search(line):
        if accumulator.state in {"unknown", "pending", "unavailable"}:
            accumulator.state = "quick_login"
        _note_event(accumulator, timestamp, timestamp_is_real)
        return

    if _PICTURE_REFRESH.search(line) or _PICTURE_REQUESTED.search(line):
        if accumulator.state in {"unknown", "unavailable"}:
            accumulator.state = "pending"
        _note_event(accumulator, timestamp, timestamp_is_real)
        return


def _build_hot_update(accumulator: _Accumulator) -> Optional[HotUpdateSnapshot]:
    """Turn the accumulated hot-update facts into a snapshot, or ``None``.

    ``None`` 表示日志里**没有任何**热更新行——与 ``up_to_date``（检查过、无需更新）
    是两件事：前者可能只是日志没挂全。
    """
    state = accumulator.hot_update_state
    if state is None:
        return None
    started = accumulator.hot_update_started_at
    completed = accumulator.hot_update_completed_at
    duration: Optional[float] = None
    if started is not None and completed is not None:
        # 负值（跨零点、日志乱序）丢弃而不是钳到 0：0 会被读成「瞬间完成」。
        span = (completed - started).total_seconds()
        duration = span if span >= 0 else None
    return HotUpdateSnapshot(
        state=state,
        target_version=accumulator.hot_update_version,
        started_at=started,
        completed_at=completed,
        duration_seconds=duration,
        remediation=_HOT_UPDATE_REMEDIATION.get(state),
    )


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

    hot_update = _build_hot_update(accumulator)

    state = accumulator.state
    failure_reason = accumulator.failure_reason
    # 只在时间戳真实时才把它交出去。回落值是「读日志的时刻」，
    # 交出去等于宣称「刚刚发生过一件事」，而我们并不知道它何时发生。
    last_event_at = (
        accumulator.last_event_at if accumulator.last_event_at_is_real else None
    )

    if generated_at is not None and not accumulator.generated_at_is_real:
        # 生成时刻是编的（日志那几行没有时间戳），据它算出的剩余时间必然是满额。
        # 唯一诚实的答复是「不知道」：报「还剩 120 秒」会让用户去扫一张可能早就
        # 死掉的码，然后怀疑是自己扫错了。`validity` 仍然给出——`expireTime= 120`
        # 是真的，它回答「这种码能撑多久」，与「这张还剩多久」是两个问题。
        #
        # 已扫码 / 已成功 / 已失败不受影响：那些状态与有效期无关。
        if state == "waiting_scan":
            state = "age_unknown"
        return QRLoginSnapshot(
            state=state,
            generated_at=None,
            expires_at=None,
            validity_seconds=validity,
            remaining_seconds=None,
            latest_qr_path=accumulator.latest_qr_path,
            refresh_count=max(0, accumulator.picture_count - 1),
            failure_reason=failure_reason,
            last_event_at=last_event_at,
            remediation=_REMEDIATION.get(state),
            hot_update=hot_update,
        )

    expires_at = (
        generated_at + timedelta(seconds=validity)
        if generated_at is not None and validity is not None
        else None
    )

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
        last_event_at=last_event_at,
        remediation=_REMEDIATION.get(state),
        hot_update=hot_update,
    )
