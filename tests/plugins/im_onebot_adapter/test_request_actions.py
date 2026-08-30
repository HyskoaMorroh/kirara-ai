"""需求 11：好友申请与入群邀请必须有处置出口。

`_handle_request` 已经在记录这两类事件（此前连订阅都没有），日志里写着
「待人工处理，请在 QQ 客户端或上游 WebUI 处理」。可 OneBot 协议本来就有
`set_friend_add_request` / `set_group_add_request`——只是本项目没有任何方法调用它，
于是一个部署好的机器人只能干看着申请堆积，处置得回到手机上做。

**框架依然不自动同意**：自动接受入群邀请是一个安全决定，不该由框架代替部署者做。
这里补的是「部署者可以决定」的能力，不是「框架替你决定」。

`flag` 是这两个动作的关键：OneBot 用它标识具体哪一条申请，取自请求事件本身。
不记录、不回传 `flag`，就等于把处置能力锁死在日志里。
"""

from __future__ import annotations

from typing import Any

import pytest

from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig


class _Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)

    def warning(self, message: str) -> None:
        self.messages.append(message)

    def debug(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        self.messages.append(message)


def _adapter() -> tuple[OneBotAdapter, list[tuple[str, dict]]]:
    adapter = object.__new__(OneBotAdapter)
    adapter.logger = _Logger()
    adapter.config = OneBotConfig()
    adapter.connections = {}
    calls: list[tuple[str, dict]] = []

    async def call_action(action: str, **params: Any) -> dict:
        calls.append((action, params))
        return {"status": "ok"}

    adapter._call_action = call_action  # type: ignore[method-assign]
    return adapter, calls


@pytest.mark.asyncio
async def test_a_friend_request_can_be_approved():
    adapter, calls = _adapter()

    await adapter.approve_friend_request("flag-abc")

    assert calls == [
        (
            "set_friend_add_request",
            {"flag": "flag-abc", "approve": True, "remark": "", "self_id": None},
        )
    ]


@pytest.mark.asyncio
async def test_a_friend_request_can_be_rejected():
    adapter, calls = _adapter()

    await adapter.reject_friend_request("flag-abc")

    assert calls[0][0] == "set_friend_add_request"
    assert calls[0][1]["approve"] is False


@pytest.mark.asyncio
async def test_a_group_request_requires_its_sub_type():
    """`add`（申请加群）与 `invite`（邀请入群）是两件不同的事。

    OneBot 用 `sub_type` 区分它们，传错会让动作静默不生效——上游收到一个
    它无法匹配的请求，返回成功但什么都没做。默认值在这里是有害的。
    """
    adapter, calls = _adapter()

    await adapter.approve_group_request("flag-x", sub_type="invite")

    assert calls[0][0] == "set_group_add_request"
    assert calls[0][1]["sub_type"] == "invite"
    assert calls[0][1]["approve"] is True


@pytest.mark.asyncio
async def test_an_unknown_group_sub_type_is_rejected_before_the_call():
    """未知 `sub_type` 必须在发出前就被拒绝。

    发出去只会得到一个「成功但没生效」的响应，那是最难排查的一类失败。
    """
    adapter, calls = _adapter()

    with pytest.raises(ValueError, match="sub_type"):
        await adapter.approve_group_request("flag-x", sub_type="nonsense")

    assert not calls


@pytest.mark.asyncio
async def test_rejecting_a_group_request_can_carry_a_reason():
    adapter, calls = _adapter()

    await adapter.reject_group_request("flag-x", sub_type="add", reason="人数已满")

    assert calls[0][1]["approve"] is False
    assert calls[0][1]["reason"] == "人数已满"


@pytest.mark.asyncio
async def test_an_empty_flag_is_rejected():
    """空 `flag` 匹配不到任何申请，发出去只是浪费一次往返。"""
    adapter, calls = _adapter()

    with pytest.raises(ValueError, match="flag"):
        await adapter.approve_friend_request("   ")

    assert not calls


@pytest.mark.asyncio
async def test_multi_account_deployments_must_name_the_target_account():
    """多账号下不指定 `self_id` 必须拒绝，而不是路由到任意一个账号。

    同意一个入群邀请是有副作用的动作；用错账号同意等于让另一个机器人进了群。
    """
    adapter, calls = _adapter()
    adapter.connections = {"10001": {}, "10002": {}}

    with pytest.raises(ValueError, match="self_id"):
        await adapter.approve_friend_request("flag-abc")

    assert not calls


@pytest.mark.asyncio
async def test_the_request_log_carries_the_flag_needed_to_act_on_it():
    """日志必须带上 `flag`，否则「可以处置」在实践中依然做不到。

    处置动作需要 `flag`，而运维唯一能看到事件的地方就是日志。
    只记「有一条好友申请」等于把处置能力锁死。
    """
    adapter, _ = _adapter()

    await adapter._handle_request(
        {
            "request_type": "friend",
            "flag": "flag-in-log",
            "user_id": 200,
        }
    )

    assert any("flag-in-log" in message for message in adapter.logger.messages)
