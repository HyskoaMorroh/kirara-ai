"""六个渠道的 `channel_type` 必须由适配器**显式声明**，不能靠类名推导。

`ChannelContext.from_message` 有一条回落：适配器没有 `channel_type` 时，
用类名去掉 `Adapter` 后缀再小写（`core.py:117-122`）。今天这条回落恰好给出正确
结果，因为 `OneBotAdapter` → `onebot`、`WecomAdapter` → `wecom`、
`TelegramAdapter` → `telegram`、`QQBotAdapter` → `qqbot` 都刚好命中
`SUPPORTED_CHANNEL_TYPES`。

但这是一次巧合，不是契约。任何一次类名重构（`WecomAdapter` → `WeComAdapter`
就够了：推导结果变成 `wecom` 之外的 `wecomadapter`？不——是 `wecom`，
但 `QQBotAdapter` → `QQOfficialBotAdapter` 会变成 `qqofficialbot`）都会让：

* 该渠道的所有 Agent 绑定**静默失效**——绑定表里存的是旧值，
  运行时算出新值，两边对不上，请求退回全局默认 Agent；
* 会话键跟着漂移，历史上下文断开。

两者都不报错。`http` 渠道曾经就是这样：类名推导出 `httplegacy`，而枚举里是
`http`，绑定请求被拒，该入口只能用全局默认 Agent——直到有人去查为什么给它单独配
的模型链没生效。

这些用例把「显式声明」钉成源码级契约：新增或重命名适配器时，
忘了声明会让这里直接红，而不是等到生产上有人发现绑定不生效。
"""

from __future__ import annotations

import importlib

import pytest

from kirara_ai.agent_runtime.core import SUPPORTED_CHANNEL_TYPES

#: 适配器模块 → 类名 → 期望的渠道类型。
#: 期望值取自 `SUPPORTED_CHANNEL_TYPES`，不是从类名算出来的——
#: 从类名算等于用被测逻辑验证被测逻辑。
ADAPTERS = (
    ("kirara_ai.plugins.im_onebot_adapter.adapter", "OneBotAdapter", "onebot"),
    ("kirara_ai.plugins.im_qqbot_adapter.adapter", "QQBotAdapter", "qqbot"),
    ("kirara_ai.plugins.im_telegram_adapter.adapter", "TelegramAdapter", "telegram"),
    ("kirara_ai.plugins.im_wecom_adapter.adapter", "WecomAdapter", "wecom"),
    ("kirara_ai.plugins.im_http_legacy_adapter.adapter", "HttpLegacyAdapter", "http"),
    ("kirara_ai.web.api.llm.webui_adapter", "WebUIAdapter", "webui"),
)


@pytest.mark.parametrize(("module_name", "class_name", "expected"), ADAPTERS)
def test_adapter_declares_its_channel_type(module_name: str, class_name: str, expected: str):
    adapter_class = getattr(importlib.import_module(module_name), class_name)

    declared = adapter_class.__dict__.get("channel_type") or getattr(
        adapter_class, "channel_type", None
    )
    # 回归点：四个适配器此前没有这个属性，靠类名推导凑巧对上。
    assert declared == expected


def test_every_supported_channel_type_has_exactly_one_declaring_adapter():
    declared = {}
    for module_name, class_name, _ in ADAPTERS:
        adapter_class = getattr(importlib.import_module(module_name), class_name)
        value = getattr(adapter_class, "channel_type", None)
        declared.setdefault(value, []).append(class_name)

    # 枚举里的每个渠道都要有归属：一个没有适配器声明的枚举值是一条
    # 「可以绑定但永远收不到消息」的路径，而界面上它看起来是可用的。
    assert set(declared) == set(SUPPORTED_CHANNEL_TYPES)
    duplicated = {value: names for value, names in declared.items() if len(names) > 1}
    # 两个适配器声明同一个渠道类型会让按渠道绑定的 Agent 同时命中两个入口，
    # 而运维只配了其中一个。
    assert duplicated == {}


@pytest.mark.parametrize(("module_name", "class_name", "expected"), ADAPTERS)
def test_declared_value_does_not_depend_on_the_class_name(
    module_name: str, class_name: str, expected: str
):
    adapter_class = getattr(importlib.import_module(module_name), class_name)

    # 显式声明的意义就在于「改类名不影响渠道身份」。这条用例伪造一次重命名：
    # 用一个子类换掉类名，声明值必须原样继承下来。
    renamed = type("RenamedSomethingElseAdapter", (adapter_class,), {})
    assert getattr(renamed, "channel_type", None) == expected
