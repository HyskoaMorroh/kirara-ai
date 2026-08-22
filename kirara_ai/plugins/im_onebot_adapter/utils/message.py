"""Convert OneBot V11 message segments to Kirara message elements."""

from __future__ import annotations

from typing import Any, Optional

from kirara_ai.im.message import (
    EmojiMessage,
    FileMessage,
    ImageMessage,
    JsonMessage,
    MediaMessage,
    MentionElement,
    MessageElement,
    ReplyElement,
    TextMessage,
    VideoMessage,
    VoiceMessage,
)
from kirara_ai.im.sender import ChatSender


def create_message_element(
    msg_type: str,
    data: dict[str, Any],
    logger: Any,
    *,
    self_id: Optional[str] = None,
    media_data: Optional[bytes] = None,
) -> Optional[MessageElement | MediaMessage]:
    """Convert one OneBot segment, ignoring unsupported optional segments."""
    try:
        if msg_type == "text":
            return TextMessage(str(data.get("text", "")))
        if msg_type == "at":
            target = str(data.get("qq", ""))
            if self_id is not None and target == str(self_id):
                return MentionElement(ChatSender.get_bot_sender())
            return None
        if msg_type == "reply":
            message_id = data.get("id")
            return ReplyElement(str(message_id)) if message_id is not None else None
        if msg_type in {"image", "file", "record", "video"}:
            if media_data is not None:
                if msg_type == "image":
                    return ImageMessage(data=media_data)
                if msg_type == "record":
                    return VoiceMessage(data=media_data)
                if msg_type == "video":
                    return VideoMessage(data=media_data)
                return FileMessage(data=media_data)
            url = data.get("url") or data.get("path")
            if not url:
                return None
            if msg_type == "image":
                return ImageMessage(url=str(url), format=data.get("file"))
            if msg_type == "record":
                return VoiceMessage(url=str(url), format=data.get("file"))
            if msg_type == "video":
                return VideoMessage(url=str(url), format=data.get("file"))
            return FileMessage(url=str(url), format=data.get("file"))
        if msg_type == "face":
            face_id = data.get("id")
            return EmojiMessage(str(face_id)) if face_id is not None else None
        if msg_type == "json":
            return JsonMessage(str(data.get("data", "")))
    except Exception as exc:
        logger.warning(f"OneBot 消息段转换失败 type={msg_type}: {exc}")
    return None
