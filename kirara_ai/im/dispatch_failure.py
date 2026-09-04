"""把一次派发失败翻译成用户能照做的一句话。

为什么要抽出来
------------
四个 IM 适配器各自处理派发异常，而它们说的话完全不一样：

* 企业微信有一套按错误类型分派的映射（超时 / 认证 / 限流 / 网络）；
* Telegram 是全项目**唯一**暴露给用户的英文报错
  （`Workflow execution failed, please try again later: <原始异常>`）；
* OneBot 与 QQ 官方机器人只记日志，用户那侧什么都没有。

同一个失败在三个渠道上呈现成三种样子，而用户往往同时接了两个渠道——
「Telegram 说超时，QQ 什么都不说」会让人以为是渠道的问题，
而实际是同一个上游在同一时刻不可用。

这个模块只做一件事：`Exception -> 一句中文`。渲染与发送仍留在各适配器里
（它们的消息长度限制、能不能主动发消息、要不要回引原消息都不同）。

刻意不做的事
----------
**不吞掉原始信息。** 每条都附上截断后的原始异常文本：运维需要它去搜日志，
而一句纯粹的「请稍后重试」会把可诊断的失败变成不可诊断的。

**不猜测重试是否有用。** 「配置缺失」这类重试永远不会成功，文案直接说去改配置；
而超时、限流这类给的是「稍后再试」。把两者说成同一句话，会让用户在一个
必然失败的动作上反复尝试。
"""

from __future__ import annotations

#: 原始异常文本的截断长度。
#:
#: 保留它是为了可诊断（运维拿它去搜日志），截断是因为上游有时把整个 HTML
#: 错误页塞进异常字符串，而那会撑爆一条 IM 消息。
_DETAIL_LIMIT = 300


def describe_dispatch_failure(error: BaseException) -> str:
    """把派发异常翻译成一句用户能照做的中文。

    判据按「用户接下来该做什么」分组，而不是按异常类型的继承关系分——
    后者会把「认证失败」和「参数错误」归到一起，而这两件事一个要去改凭据、
    一个要去改请求。
    """

    from kirara_ai.workflow.core.dispatch.exceptions import AgentConfigurationNotFound

    detail = str(error).strip()
    truncated = detail[:_DETAIL_LIMIT]

    # 配置缺失：重试永远不会成功，因此不说「稍后再试」。
    # 这一支放最前面：它的消息本身已经写清了该去哪、做什么。
    if isinstance(error, AgentConfigurationNotFound):
        return truncated or "没有可用的 Agent，请先在「模型与 Agent」里配置。"

    lowered = detail.lower()
    if "524" in detail or "timeout" in lowered or "timed out" in lowered:
        return (
            "请求超时：模型服务响应时间过长，备用链已全部尝试。"
            f"请稍后再试或检查上游可用性。\n详细信息：{truncated}"
        )
    if "401" in detail or "403" in detail:
        return (
            "认证失败：API 密钥无效或没有权限。"
            f"请在「模型管理」里检查该供应商的凭据。\n详细信息：{truncated}"
        )
    if "429" in detail:
        return (
            "请求过于频繁：已触发上游速率限制，请稍后再试。"
            f"\n详细信息：{truncated}"
        )
    if "connection" in lowered or "network" in lowered or "unreachable" in lowered:
        return (
            "网络错误：连不上模型服务。"
            f"请检查服务器出网与上游地址。\n详细信息：{truncated}"
        )
    return f"消息处理失败：{truncated or type(error).__name__}"
