from abc import ABC, abstractmethod
from io import BytesIO
from typing import TYPE_CHECKING, Any
import re

from wechatpy.messages import BaseMessage

from kirara_ai.logger import get_logger

if TYPE_CHECKING:
    from .adapter import WecomConfig


def markdown_to_plain_text(text: str) -> str:
    """
    将 Markdown 格式转换为适合企业微信显示的纯文本格式
    保留结构但去除 Markdown 语法标记
    """
    # 标题转换：## 标题 → ━━ 标题 ━━
    text = re.sub(r'^### (.+)$', r'▸ \1', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'\n━━ \1 ━━', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'\n━━━ \1 ━━━', text, flags=re.MULTILINE)

    # 粗体：**文本** → 「文本」
    text = re.sub(r'\*\*(.+?)\*\*', r'「\1」', text)

    # 表格转换为对齐文本
    lines = text.split('\n')
    result = []
    in_table = False

    for line in lines:
        # 检测表格行（包含 |）
        if '|' in line and not line.strip().startswith('|---'):
            if not in_table:
                in_table = True
                result.append('')  # 表格前空行
            # 分割单元格并清理
            cells = [cell.strip() for cell in line.split('|') if cell.strip()]
            # 简化格式：去掉边框，用空格分隔
            result.append('  '.join(cells))
        elif line.strip().startswith('|---'):
            # 跳过分隔行
            continue
        else:
            if in_table:
                result.append('')  # 表格后空行
                in_table = False
            result.append(line)

    text = '\n'.join(result)

    # 列表项：- 项目 → • 项目
    text = re.sub(r'^\s*-\s+', '• ', text, flags=re.MULTILINE)

    # 行内代码：`代码` → 『代码』
    text = re.sub(r'`([^`]+)`', r'『\1』', text)

    # 代码块：```...``` → ［代码］...［/代码］
    text = re.sub(r'```[\w]*\n(.*?)\n```', r'［代码］\n\1\n［/代码］', text, flags=re.DOTALL)

    # 链接：[文本](url) → 文本 (url)
    text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'\1 (\2)', text)

    # 删除多余空行（超过2个连续换行压缩为2个）
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def split_long_message(text: str, max_length: int = 1800) -> list[str]:
    """
    将超长消息按段落智能分割，避免超过企业微信2048字节上限
    max_length 设为 1800 字节留出安全边界（中文字符占3字节）
    """
    # 如果不超长，直接返回
    if len(text.encode('utf-8')) <= max_length:
        return [text]

    chunks = []
    current_chunk = []
    current_size = 0

    # 按段落分割（保留双换行分隔的结构）
    paragraphs = text.split('\n\n')

    for para in paragraphs:
        para_size = len(para.encode('utf-8'))

        # 单个段落超长，需要按行拆分
        if para_size > max_length:
            if current_chunk:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = []
                current_size = 0

            lines = para.split('\n')
            line_chunk = []
            line_size = 0

            for line in lines:
                line_bytes = len(line.encode('utf-8'))
                if line_size + line_bytes + 1 > max_length:  # +1 for '\n'
                    if line_chunk:
                        chunks.append('\n'.join(line_chunk))
                    line_chunk = [line]
                    line_size = line_bytes
                else:
                    line_chunk.append(line)
                    line_size += line_bytes + 1

            if line_chunk:
                chunks.append('\n'.join(line_chunk))

        # 段落可以追加到当前块
        elif current_size + para_size + 2 <= max_length:  # +2 for '\n\n'
            current_chunk.append(para)
            current_size += para_size + 2

        # 段落无法追加，保存当前块并开启新块
        else:
            if current_chunk:
                chunks.append('\n\n'.join(current_chunk))
            current_chunk = [para]
            current_size = para_size

    # 保存最后一个块
    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))

    # 为分段消息添加序号（总数>1时）
    if len(chunks) > 1:
        return [f"[{i+1}/{len(chunks)}]\n{chunk}" for i, chunk in enumerate(chunks)]

    return chunks

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
