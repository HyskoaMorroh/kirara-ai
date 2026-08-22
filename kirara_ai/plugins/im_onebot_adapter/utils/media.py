"""Security boundary for media URLs supplied by OneBot events."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import socket
from typing import Any
from urllib.parse import unquote_to_bytes, urljoin, urlparse

import aiohttp


REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _require_public_address(value: str) -> None:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError as exc:
        raise ValueError(f"无法验证媒体地址：{value}") from exc
    if not address.is_global:
        raise ValueError(f"OneBot 入站媒体指向非公网地址：{value}")


def validate_public_media_url(url: str) -> str:
    """Validate URL syntax before DNS resolution or an HTTP request."""
    value = str(url).strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("OneBot 入站媒体仅允许 HTTP/HTTPS 公网地址")
    if not parsed.hostname:
        raise ValueError("OneBot 入站媒体 URL 缺少主机名")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("OneBot 入站媒体 URL 不允许包含认证信息")
    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("OneBot 入站媒体指向非公网地址：localhost")
    try:
        ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        pass
    else:
        _require_public_address(hostname)
    return value


def decode_inline_media(value: str, *, max_bytes: int) -> bytes:
    """Decode a bounded data URL or OneBot ``base64://`` payload."""
    if value.startswith("base64://"):
        encoded = value[len("base64://") :]
    else:
        if not value.startswith("data:"):
            raise ValueError("不是受支持的内联媒体格式")
        try:
            header, encoded = value.split(",", 1)
        except ValueError as exc:
            raise ValueError("内联媒体格式无效") from exc
        if ";base64" not in header.casefold():
            raise ValueError("内联媒体必须使用 Base64 编码")
    try:
        payload = base64.b64decode(unquote_to_bytes(encoded), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("内联媒体 Base64 数据无效") from exc
    if len(payload) > max_bytes:
        raise ValueError(f"OneBot 入站媒体超过大小上限（{max_bytes} 字节）")
    return payload


class PublicResolver:
    """Reject a hostname if any DNS answer is not globally routable."""

    def __init__(self, resolver: Any | None = None):
        self._resolver = resolver or aiohttp.resolver.DefaultResolver()

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict[str, Any]]:
        results = await self._resolver.resolve(host, port, family)
        if not results:
            raise ValueError(f"OneBot 入站媒体主机无法解析：{host}")
        for result in results:
            _require_public_address(str(result["host"]))
        return results

    async def close(self) -> None:
        await self._resolver.close()


async def download_public_media(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    max_redirects: int = 3,
) -> bytes:
    """Download public HTTP media with bounded redirects, time and size."""
    if max_bytes <= 0:
        raise ValueError("媒体大小上限必须大于 0")
    if timeout_seconds <= 0:
        raise ValueError("媒体下载超时必须大于 0")

    resolver = PublicResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
    timeout = aiohttp.ClientTimeout(
        total=timeout_seconds,
        connect=min(timeout_seconds, 5.0),
        sock_read=timeout_seconds,
    )
    current_url = validate_public_media_url(url)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        trust_env=False,
    ) as session:
        for redirect_count in range(max_redirects + 1):
            async with session.get(current_url, allow_redirects=False) as response:
                if response.status in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError("OneBot 入站媒体重定向缺少 Location")
                    if redirect_count >= max_redirects:
                        raise ValueError("OneBot 入站媒体重定向次数超过上限")
                    current_url = validate_public_media_url(
                        urljoin(current_url, location)
                    )
                    continue
                if response.status < 200 or response.status >= 300:
                    raise ValueError(
                        f"OneBot 入站媒体下载失败，HTTP {response.status}"
                    )
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise ValueError("OneBot 入站媒体 Content-Length 无效") from exc
                    if declared_size > max_bytes:
                        raise ValueError(
                            f"OneBot 入站媒体超过大小上限（{max_bytes} 字节）"
                        )

                chunks: list[bytes] = []
                received = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    received += len(chunk)
                    if received > max_bytes:
                        raise ValueError(
                            f"OneBot 入站媒体超过大小上限（{max_bytes} 字节）"
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
    raise ValueError("OneBot 入站媒体下载未返回内容")
