"""The WebUI adapter for the shared IM and Agent runtime boundary."""

from __future__ import annotations

from typing import Any

from kirara_ai.im.adapter import IMAdapter
from kirara_ai.im.message import IMMessage


class WebUIAdapter(IMAdapter):
    """Capture one request's runtime reply without sharing mutable state."""

    channel_type = "webui"
    adapter_type = "webui"
    adapter_instance = "webui"
    account_scope = "webui"
    is_running = True
    llm_manager = None

    def __init__(self, *, session_agent_id: str | None = None) -> None:
        self.session_agent_id = session_agent_id
        self.reply: IMMessage | None = None

    async def convert_to_message(self, raw_message: Any) -> IMMessage:
        if not isinstance(raw_message, IMMessage):
            raise TypeError("WebUIAdapter accepts normalized IMMessage values")
        return raw_message

    async def send_message(self, message: IMMessage, recipient: Any) -> None:
        self.reply = message

    async def start(self) -> None:
        self.is_running = True

    async def stop(self) -> None:
        self.is_running = False
