from abc import ABC, abstractmethod
from io import BytesIO
from typing import TYPE_CHECKING, Any
import re

from wechatpy.messages import BaseMessage

from kirara_ai.im.text_render import (degrade_math, display_width, is_table_row,
                                      is_table_separator,
                                      paginate_with_truncation_notice,
                                      parse_table_row, render_table)
from kirara_ai.logger import get_logger

if TYPE_CHECKING:
    from .adapter import WecomConfig


def _display_width(text: str) -> int:
    """
    计算字符串在等宽字体下的显示宽度，中日韩全角字符按 2 列计算。
    用于表格列对齐，避免中英混排时表格线错位。
    """
    return display_width(text)


def _render_table(rows: list[list[str]], has_header: bool = True) -> list[str]:
    """
    将表格数据渲染为带完整边框线的等宽文本表格。
    第一行视为表头，使用 ├─┼─┤ 分隔，保证结构清晰可读。

    走共享的 ``render_table``：宽表会自动降级成纵向字段布局。企业微信同样
    没有可靠的等宽字体，一张 8 列中文表的框线在手机端一定错位。
    """
    return render_table(rows, has_header=has_header)


def markdown_to_plain_text(text: str) -> str:
    """
    将 Markdown 格式转换为适合企业微信显示的纯文本格式
    保留结构但去除 Markdown 语法标记

    处理顺序很关键：先把围栏代码块摘出来占位，避免代码内部的
    #、**、-、` 等字符被后续的 Markdown 规则误替换、破坏缩进。
    """
    # 第一步：摘出围栏代码块（```lang ... ```），原样保留内部缩进
    code_blocks: list[str] = []

    def _stash_code_block(match: re.Match) -> str:
        lang = (match.group(1) or "").strip()
        body = match.group(2)
        # 去掉代码块整体的公共缩进前先保留原样，仅去掉结尾多余换行
        body = body.rstrip("\n")
        header = f"［代码 {lang}］" if lang else "［代码］"
        code_blocks.append(f"{header}\n{body}\n［/代码］")
        return f"\x00CODE{len(code_blocks) - 1}\x00"

    text = re.sub(r'```([\w+-]*)\n(.*?)```', _stash_code_block, text, flags=re.DOTALL)

    # 数学降级走共享实现。此前 WeCom 路径完全跳过这一步，于是同一段模型回复
    # 在 QQ 上是 `T → 0`、在企业微信上是原始的 `$T \to 0$`——平台差异应该只在
    # 渲染层，不该表现为「有的平台处理了、有的没有」。
    # 代码块此时已被占位符替换，因此不会被波及。
    text = degrade_math(text)

    # 标题转换：## 标题 → ━━ 标题 ━━
    text = re.sub(r'^### (.+)$', r'▸ \1', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'\n━━ \1 ━━', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'\n━━━ \1 ━━━', text, flags=re.MULTILINE)

    # 粗体：**文本** → 「文本」
    text = re.sub(r'\*\*(.+?)\*\*', r'「\1」', text)

    # 斜体/强调：*文本* 或 _文本_ → 文本（企业微信不支持样式，仅去掉标记）
    text = re.sub(r'(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])', r'\1', text)
    text = re.sub(r'(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])', r'\1', text)

    # 删除线：~~文本~~ → 文本
    text = re.sub(r'~~(.+?)~~', r'\1', text)

    # 引用块：> 文本 → ┃ 文本
    text = re.sub(r'^\s{0,3}>\s?', '┃ ', text, flags=re.MULTILINE)

    # 表格转换为带边框的对齐文本表格
    lines = text.split('\n')
    result: list[str] = []
    table_rows: list[list[str]] = []
    # 是否见过 `---` 分隔行：没有它就不能断言第一行是表头，
    # 宽表降级时也就不能拿它当字段名。
    saw_separator = False

    def flush_table():
        nonlocal saw_separator
        if table_rows:
            result.append('')  # 表格前空行
            result.extend(_render_table(table_rows, has_header=saw_separator))
            result.append('')  # 表格后空行
            table_rows.clear()
        saw_separator = False

    for line in lines:
        stripped = line.strip()
        # 分隔行（|---|---|）只用于标记表头，不参与渲染
        if is_table_separator(stripped):
            saw_separator = True
            continue
        # 检测表格行（含 | 且不是代码占位）
        if is_table_row(stripped) and '\x00CODE' not in stripped:
            table_rows.append(parse_table_row(stripped))
        else:
            flush_table()
            result.append(line)
    flush_table()

    text = '\n'.join(result)

    # 列表项：- 项目 → • 项目（保留原有层级缩进）
    text = re.sub(r'^(\s*)[-*+]\s+', r'\1• ', text, flags=re.MULTILINE)

    # 行内代码：`代码` → 『代码』（此时围栏代码块已被占位，不会被误伤）
    text = re.sub(r'`([^`\n]+)`', r'『\1』', text)

    # 链接：[文本](url) → 文本 (url)
    text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'\1 (\2)', text)

    # 删除多余空行（超过2个连续换行压缩为2个）
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 最后一步：还原代码块，保持原始缩进
    for index, block in enumerate(code_blocks):
        text = text.replace(f"\x00CODE{index}\x00", block)

    return text.strip()


def _split_table_block(lines: list[str], max_length: int) -> list[str]:
    """
    拆分超长表格：每段都补齐上边框、表头、分隔线与下边框，
    保证每条消息里的表格都是结构完整的，不会出现半张表。

    分段主路径已统一到 ``kirara_ai.im.text_render.split_structured_text``；
    本函数保留为公开行为的兼容入口（外部插件可能直接调用），
    并作为「表格必须整段带框」这一约定的可执行说明。
    """
    top = lines[0] if lines and lines[0].startswith("┌") else ""
    bottom = lines[-1] if lines and lines[-1].startswith("└") else ""
    header = ""
    separator = ""
    body_start = 1 if top else 0
    if len(lines) > body_start and lines[body_start].startswith("│"):
        header = lines[body_start]
        body_start += 1
    if len(lines) > body_start and lines[body_start].startswith("├"):
        separator = lines[body_start]
        body_start += 1
    body = [line for line in lines[body_start:] if not line.startswith("└")]

    frame = [line for line in (top, header, separator) if line]
    frame_size = sum(len(line.encode("utf-8")) + 1 for line in frame)
    frame_size += len(bottom.encode("utf-8")) + 1 if bottom else 0

    chunks: list[str] = []
    current: list[str] = []
    current_size = frame_size
    for row in body:
        row_size = len(row.encode("utf-8")) + 1
        if current and current_size + row_size > max_length:
            chunks.append("\n".join(frame + current + ([bottom] if bottom else [])))
            current = [row]
            current_size = frame_size + row_size
        else:
            current.append(row)
            current_size += row_size
    if current:
        chunks.append("\n".join(frame + current + ([bottom] if bottom else [])))
    return chunks


def _split_code_block(lines: list[str], max_length: int) -> list[str]:
    """
    拆分超长代码块：每段都补齐 ［代码］/［/代码］ 标记，
    并保持每行原始缩进，避免代码在分段处断裂。

    与 :func:`_split_table_block` 同理，分段主路径已统一到共享实现，
    这里保留为兼容入口与围栏约定的可执行说明。
    """
    head = lines[0] if lines and lines[0].startswith("［代码") else "［代码］"
    tail = "［/代码］"
    body = [line for line in lines if not line.startswith("［代码") and line != tail]

    frame_size = len(head.encode("utf-8")) + len(tail.encode("utf-8")) + 2
    chunks: list[str] = []
    current: list[str] = []
    current_size = frame_size
    for line in body:
        line_size = len(line.encode("utf-8")) + 1
        if current and current_size + line_size > max_length:
            chunks.append("\n".join([head] + current + [tail]))
            current = [line]
            current_size = frame_size + line_size
        else:
            current.append(line)
            current_size += line_size
    if current:
        chunks.append("\n".join([head] + current + [tail]))
    return chunks


def split_long_message(text: str, max_length: int = 1800) -> list[str]:
    """Split WeCom plain text by UTF-8 bytes with complete structures.

    企业微信曾经有一套自己的分段实现（按段落切分、表格与代码块各自补边框，
    并给分段加 ``[i/N]`` 前缀）。它与 ``kirara_ai/im/text_render.py`` 的
    ``split_structured_text`` 职责完全重叠，且页码格式与其余渠道的
    「第 N 页 / 共 M 页」不一致——同一个机器人在不同 APP 上给出两种页码写法。
    现在统一走共享实现，页码格式随之统一；WeCom 独有的部分只剩
    ``code_style="wecom"``（``［代码］`` 围栏）这一个渲染差异。

    超出页数上限时截断并追加提示，而不是抛 ``ValueError``：异常会一路穿出
    发送路径，用户什么都收不到（需求 19.4「全部发送、内容不得丢失」）。
    """
    pages, _truncated = paginate_with_truncation_notice(
        text,
        max_bytes=max_length,
        max_total_bytes=None,
        code_style="wecom",
    )
    return pages

class WechatApiDelegate(ABC):
    """微信API代理接口，用于处理不同类型的微信API调用"""

    @abstractmethod
    def setup_api(self, config: "WecomConfig"):
        """设置API相关组件"""

    @abstractmethod
    def check_signature(self, signature: str, timestamp: str, nonce: str, echo_str: str) -> str:
        """验证签名"""

    @abstractmethod
    def decrypt_message(self, message: bytes, signature: str, timestamp: str, nonce: str) -> str:
        """解密消息"""

    @abstractmethod
    def parse_message(self, message: str) -> BaseMessage:
        """解析消息"""

    @abstractmethod
    async def send_text(self, app_id: str, user_id: str, text: str) -> Any:
        """发送文本消息"""

    @abstractmethod
    async def send_media(self, app_id: str, user_id: str, media_type: str, media_bytes: BytesIO) -> Any:
        """发送媒体消息"""


class CorpWechatApiDelegate(WechatApiDelegate):
    """企业微信API代理实现"""

    def setup_api(self, config: "WecomConfig"):
        """设置企业微信API相关组件"""
        from wechatpy.enterprise import parse_message
        from wechatpy.enterprise.client import WeChatClient
        from wechatpy.enterprise.crypto import WeChatCrypto

        self.crypto = WeChatCrypto(
            config.token, config.encoding_aes_key, config.corp_id
        )
        self.client = WeChatClient(config.corp_id, config.secret)
        self.parse_message_func = parse_message
        self.logger = get_logger("CorpWechatApiDelegate")

    def check_signature(self, signature: str, timestamp: str, nonce: str, echo_str: str) -> str:
        """验证企业微信签名"""
        return self.crypto.check_signature(signature, timestamp, nonce, echo_str)

    def decrypt_message(self, message: bytes, signature: str, timestamp: str, nonce: str) -> str:
        """解密企业微信消息"""
        return self.crypto.decrypt_message(message, signature, timestamp, nonce)

    def parse_message(self, message: str) -> BaseMessage:
        """解析企业微信消息"""
        return self.parse_message_func(message) # type: ignore

    async def send_text(self, app_id: str, user_id: str, text: str) -> Any:
        """发送企业微信文本消息（支持自动分段）"""
        # 将 Markdown 转换为纯文本格式
        plain_text = markdown_to_plain_text(text)
        # 分段发送
        chunks = split_long_message(plain_text)
        results = []
        for chunk in chunks:
            result = self.client.message.send_text(app_id, user_id, chunk)
            results.append(result)
        return results[-1] if results else None  # 返回最后一条消息的结果

    async def send_media(self, app_id: str, user_id: str, media_type: str, media_bytes: BytesIO) -> Any:
        """发送企业微信媒体消息"""
        media_id = self.client.media.upload(media_type, media_bytes)["media_id"]
        send_method = getattr(self.client.message, f"send_{media_type}")
        return send_method(app_id, user_id, media_id)


class PublicWechatApiDelegate(WechatApiDelegate):
    """公众号微信API代理实现"""

    def setup_api(self, config: "WecomConfig"):
        """设置公众号API相关组件"""
        from wechatpy import WeChatClient
        from wechatpy.crypto import WeChatCrypto
        from wechatpy.parser import parse_message

        self.crypto = WeChatCrypto(
            config.token, config.encoding_aes_key, config.app_id
        )
        self.client = WeChatClient(config.app_id, config.secret)
        self.parse_message_func = parse_message
        self.logger = get_logger("PublicWechatApiDelegate")

    def check_signature(self, signature: str, timestamp: str, nonce: str, echo_str: str) -> str:
        """验证公众号签名"""
        from wechatpy.utils import check_signature as wechat_check_signature
        wechat_check_signature(self.crypto.token, signature, timestamp, nonce)
        return echo_str

    def decrypt_message(self, message: bytes, signature: str, timestamp: str, nonce: str) -> str:
        """解密公众号消息"""
        return self.crypto.decrypt_message(message, signature, timestamp, nonce)

    def parse_message(self, message: str) -> BaseMessage:
        """解析公众号消息"""
        return self.parse_message_func(message) # type: ignore

    async def send_text(self, app_id: str, user_id: str, text: str) -> Any:
        """发送公众号文本消息（支持自动分段）"""
        # 将 Markdown 转换为纯文本格式
        plain_text = markdown_to_plain_text(text)
        # 分段发送
        chunks = split_long_message(plain_text)
        results = []
        for chunk in chunks:
            # 公众号API不需要app_id参数
            result = self.client.message.send_text(user_id, chunk)
            results.append(result)
        return results[-1] if results else None  # 返回最后一条消息的结果

    async def send_media(self, app_id: str, user_id: str, media_type: str, media_bytes: BytesIO) -> Any:
        """发送公众号媒体消息"""
        media_id = self.client.media.upload(media_type, media_bytes)["media_id"]
        send_method = getattr(self.client.message, f"send_{media_type}")
        # 公众号API不需要app_id参数
        return send_method(user_id, media_id)
