"""Kirara AI 内置 OneBot V11 适配器。"""

import os

from kirara_ai.plugin_manager.plugin import Plugin
from kirara_ai.web.app import WebServer

from .adapter import OneBotAdapter
from .config import OneBotConfig

__all__ = ["OneBotAdapter", "OneBotConfig"]


class OneBotAdapterPlugin(Plugin):
    """Register the built-in OneBot V11 adapter and its WebUI icon."""

    web_server: WebServer

    def on_load(self):
        self.im_registry.register(
            "onebot",
            OneBotAdapter,
            OneBotConfig,
            "OneBot V11 QQ",
            "通过 OneBot V11 反向 WebSocket 连接 QQ，支持私聊、群聊与长回复分页。",
            """
OneBot V11 QQ 适配器已内置，无需安装旧版 `im_onebot_adapters` 外部插件。
请将这里生成的反向 WebSocket 地址和访问 Token 填入 OneBot 实现，并持久化双方配置。
            """,
        )
        self.web_server.add_static_assets(
            "/assets/icons/im/onebot.png",
            os.path.join(os.path.dirname(__file__), "assets", "onebot.png"),
        )

    def on_start(self):
        pass

    def on_stop(self):
        if "onebot" in self.im_registry.get_all_adapters():
            self.im_registry.unregister("onebot")
