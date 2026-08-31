"""Credential field-name recognition shared by every redaction surface.

两处独立的关键词表各自演化，结果是**同一对凭据里只有一半被识别**：

- `kirara_ai/web/api/llm/routes.py` 的 `SENSITIVE_CONFIG_KEYS` 管 `GET /llm/backends`、
  `GET /llm/backends/export` 与编辑时的「留空即保留」；
- `kirara_ai/tracing/models.py` 的 `_SENSITIVE_KEYS` 管追踪落库。

`volcengine_adapter.py` 声明的两个字段正好落在缝里：`access_key_secret` 命中
`_secret` 后缀，`access_key_id`（描述「API 密钥 ID」，参与签名）两处都不命中。
于是导出文件里一半打了码、一半是明文——而看到打码的那一半，会让人相信整份
文件可以安全转发。这是最难自查的形态：不是「忘了做」，是「做了一半」。

本模块只负责**判定**，不负责替换：两个调用方的替换值本就不同（API 用空串，
因为空串在编辑时表示「保留原值」；追踪用 `[redacted]`）。判定统一之后，
再加一个供应商也不会重新分叉。

刻意不用「名字里带 key 就算凭据」这种宽判据：`sort_key`、`cache_key` 会被误判，
配置项从此在界面上永远显示为空。判据是「凭据词」加「限定词 + key」两条，
并保留一份显式的非凭据白名单（Token 用量字段名里带 token，但它们是计数器）。

本模块不导入任何 kirara_ai 子模块，因此可以被任意层安全引用。
"""

from __future__ import annotations

import re

#: 本身就是凭据的字段名部件。
#:
#: 只收单数 `token`：`tokens` 是用量计数（`max_tokens`、`prompt_tokens`），
#: 单复数在这里恰好是「凭据」与「计数器」的天然分界。
_CREDENTIAL_PARTS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "passwd",
        "password",
        "secret",
        "token",
        "apikey",
        "signature",
    }
)

#: 与 `key` 组合后构成凭据的限定词。
#:
#: 单独一个 `key` 不足以判定：`sort_key`、`cache_key`、`group_key` 都不是凭据。
#: 必须有限定词同时出现，才把这个 `key` 当成密钥。
_KEY_QUALIFIERS = frozenset(
    {
        "access",
        "api",
        "app",
        "auth",
        "bearer",
        "client",
        "encryption",
        "master",
        "private",
        "publishable",
        "refresh",
        "secret",
        "session",
        "signing",
        "subscription",
    }
)

#: 名字里带 token/key 但确定不是凭据的字段，永远不打码。
#:
#: 这些是模型用量与限额字段：把它们打码会让统计页面失去数据，
#: 而它们本身不包含任何秘密。
NON_CREDENTIAL_KEY_NAMES = frozenset(
    {
        "max_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "input_tokens",
        "output_tokens",
        "token_count",
        "tokens",
    }
)

_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]+")


def normalize_credential_key(key: object) -> str:
    """Return one canonical spelling for a field name.

    `AccessKeyId`、`access-key-id`、`Access Key ID`、`access_key_id` 是同一个字段。
    上游 API、HTTP 头和用户手写的配置各写一种，判定必须先归一化，
    否则同一个凭据换个写法就漏过去了。
    """
    text = _CAMEL_BOUNDARY.sub(r"\1_\2", str(key))
    return _NON_ALNUM.sub("_", text).strip("_").lower()


def credential_key_parts(key: object) -> tuple[str, ...]:
    """Return the normalized name split into its parts."""
    normalized = normalize_credential_key(key)
    return tuple(part for part in normalized.split("_") if part)


def is_credential_key(key: object) -> bool:
    """Whether a field name denotes a credential and must never be exposed.

    :param key: 任意写法的字段名（``AccessKeyId``、``x-api-key``、``api_token``…）。
    :return: 命中凭据判据时为真；用量计数类字段恒为假。
    """
    normalized = normalize_credential_key(key)
    if not normalized or normalized in NON_CREDENTIAL_KEY_NAMES:
        return False

    parts = tuple(part for part in normalized.split("_") if part)
    if any(part in _CREDENTIAL_PARTS for part in parts):
        return True
    if "key" in parts and any(part in _KEY_QUALIFIERS for part in parts):
        return True
    # `secret_id` / `client_id` 这类「凭据的另一半」：`secret` 已由上面命中，
    # 这里补 `<限定词>_id` 里确实属于密钥对的那部分。只认与 key/secret
    # 同源的限定词，`user_id`、`group_id`、`message_id` 不受影响。
    if parts[-1] == "id" and len(parts) > 1 and parts[0] in {"access", "secret", "signing"}:
        return True
    return False
