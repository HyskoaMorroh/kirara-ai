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
        if msg_type == "mface":
            # 市场表情（商城表情包）。此前没有映射，于是「只发了一个表情」的消息
            # 到达时元素列表为空，整条消息被当成空内容——用户看到机器人毫无反应。
            # 有图就按图片处理；只有摘要时至少给出可读占位，不能静默丢弃。
            if media_data is not None:
                return ImageMessage(data=media_data)
            url = data.get("url")
            if url:
                return ImageMessage(url=str(url), format=data.get("file"))
            summary = str(data.get("summary") or "").strip()
            return TextMessage(summary or "[表情]")
        if msg_type == "forward":
            # 合并转发的**占位**。真正展开由适配器在
            # `_expand_forward_element` 里做（需要调 `get_forward_msg`，
            # 有网络与权限成本，默认关闭）。这里给出明确占位，让下游知道
            # 「这里有一段合并转发内容」，而不是收到一条空消息。
            forward_id = data.get("id")
            suffix = f"：{forward_id}" if forward_id is not None else ""
            return TextMessage(f"[合并转发{suffix}]")
        if msg_type in {"dice", "rps"}:
            # 骰子与猜拳。QQ 客户端渲染为动画，纯文本侧只能给出结果值。
            result = data.get("result")
            label = "骰子" if msg_type == "dice" else "猜拳"
            return TextMessage(f"[{label}{'：' + str(result) if result is not None else ''}]")
        if msg_type == "shake":
            return TextMessage("[窗口抖动]")
        if msg_type == "face":
            face_id = data.get("id")
            return EmojiMessage(str(face_id)) if face_id is not None else None
        if msg_type == "json":
            return JsonMessage(str(data.get("data", "")))
        if msg_type == "markdown":
            # Markdown 段的 `content` **就是正文**，必须原样保留。
            # 换成占位是丢内容，比丢一个交互动作严重得多；
            # 排版由 `im/text_render.py` 统一处理，这里不做任何转换。
            content = str(data.get("content") or "")
            return TextMessage(content) if content else None
        if msg_type == "poke":
            # 拍一拍是一个交互动作，纯文本侧只能说明它发生了。
            return TextMessage("[拍一拍]")
        if msg_type == "location":
            # 有标题时标题比经纬度更有信息量；没有标题时经纬度是唯一的信息，
            # 必须给出而不是只说「[位置]」。
            title = str(data.get("title") or "").strip()
            if title:
                return TextMessage(f"[位置：{title}]")
            latitude = data.get("lat")
            longitude = data.get("lon")
            if latitude is not None and longitude is not None:
                return TextMessage(f"[位置：{latitude},{longitude}]")
            return TextMessage("[位置]")
        if msg_type == "contact":
            # 名片分享。群和好友两种，处置完全不同，必须分开表达。
            target = data.get("id")
            suffix = f"：{target}" if target is not None else ""
            if str(data.get("type") or "").strip().lower() == "group":
                return TextMessage(f"[推荐群{suffix}]")
            return TextMessage(f"[推荐联系人{suffix}]")
        if msg_type in {"share", "music"}:
            # 链接与音乐分享。标题是唯一值得展示的部分；
            # 刻意不映射成 ImageMessage / FileMessage——它们不是那种东西，
            # 硬映射会让下游按错误的类型处理。
            title = str(data.get("title") or "").strip()
            if title:
                return TextMessage(f"[分享：{title}]")
            return TextMessage("[链接分享]" if msg_type == "share" else "[音乐分享]")
        if msg_type == "xml":
            # 平台私有结构，不展开：解析它等于把一个会变的私有格式纳入契约。
            return TextMessage("[XML 卡片]")
        if msg_type == "anonymous":
            return TextMessage("[匿名]")
    except Exception as exc:
        logger.warning(f"OneBot 消息段转换失败 type={msg_type}: {exc}")
    return None
