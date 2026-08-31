"""供应商凭据脱敏必须覆盖真实存在的凭据字段名。

需求 21.1 要求「供应商凭据必须脱敏保存和展示」。判据来自适配器自己的字段声明，
而不是一份凭直觉写下的关键词表。

`volcengine_adapter.py` 声明了两个字段：`access_key_secret`（描述「API 密钥」）
与 `access_key_id`（描述「API 密钥 ID」，参与 HMAC 签名）。前者命中
`_secret` 后缀，后者**两处都不命中**：

- `GET /llm/backends` 明文返回 `access_key_id`；
- `GET /llm/backends/export` 把它写进那份「已脱敏、可转发可提交」的导出文件——
  同一对凭据里另一半确实被打了码，于是导出文件**看起来**是干净的；
- `_restore_unchanged_secrets` 不会为它保留旧值，编辑时前端回传什么就存什么；
- 追踪存储的 `_redact_trace_value` 同样漏掉它。

这一类缺陷的形态是「一半正确」：成对的凭据里只有一半被识别，
而看到打码的那一半会让人相信整条路径是安全的。

两份关键词表必须同时覆盖：一份管 API 响应与导出，一份管追踪落库。
"""

from __future__ import annotations

import pytest

from kirara_ai.tracing.models import _redact_trace_value
from kirara_ai.web.api.llm.routes import (_is_sensitive_config_key,
                                          _redact_sensitive_config,
                                          _restore_unchanged_secrets)

#: 适配器真实声明过、或同类 API 常见的凭据字段名。
CREDENTIAL_KEYS = (
    "access_key_id",
    "access_key_secret",
    "secret_id",
    "secret_key",
    "private_key",
    "x_api_key",
    "api-key",
    "apiKey",
    "AccessKeyId",
    "session_key",
    "api_key",
    "token",
    "authorization",
)

#: 名字里带 token/key 但不是凭据的字段，必须继续原样返回。
NON_CREDENTIAL_KEYS = (
    "max_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "model",
    "api_base",
)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_every_credential_field_is_recognized(key: str):
    assert _is_sensitive_config_key(key) is True, key


@pytest.mark.parametrize("key", NON_CREDENTIAL_KEYS)
def test_non_credential_fields_are_not_redacted(key: str):
    assert _is_sensitive_config_key(key) is False, key


def test_the_api_response_blanks_every_credential_half():
    """成对凭据的两半都必须打码，不能只打其中一半。"""
    config = {
        "access_key_id": "AKID-real",
        "access_key_secret": "SECRET-real",
        "api_base": "https://ark.example.com",
        "max_tokens": 4096,
    }

    redacted = _redact_sensitive_config(config)

    assert redacted["access_key_id"] == ""
    assert redacted["access_key_secret"] == ""
    # 非凭据字段原样保留，否则导出文件失去可用性。
    assert redacted["api_base"] == "https://ark.example.com"
    assert redacted["max_tokens"] == 4096
    # 原始对象不得被就地修改。
    assert config["access_key_id"] == "AKID-real"


def test_a_blank_credential_on_edit_keeps_the_stored_value():
    """编辑时留空表示「不修改」，两半都要享受这个语义。

    否则前端把打码后的空串回传，就会把服务器上真实的 `access_key_id` 清空。
    """
    submitted = {"access_key_id": "", "access_key_secret": "", "model": "doubao"}
    current = {
        "access_key_id": "AKID-real",
        "access_key_secret": "SECRET-real",
        "model": "doubao",
    }

    restored = _restore_unchanged_secrets(submitted, current)

    assert restored["access_key_id"] == "AKID-real"
    assert restored["access_key_secret"] == "SECRET-real"


def test_nested_provider_config_is_redacted_too():
    """凭据可能嵌在子对象或列表里。"""
    config = {
        "providers": [
            {"name": "volc", "access_key_id": "AKID-real"},
            {"name": "tencent", "secret_id": "SID-real"},
        ],
        "headers": {"x_api_key": "XK-real"},
    }

    redacted = _redact_sensitive_config(config)

    assert redacted["providers"][0]["access_key_id"] == ""
    assert redacted["providers"][1]["secret_id"] == ""
    assert redacted["headers"]["x_api_key"] == ""
    assert redacted["providers"][0]["name"] == "volc"


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_trace_storage_redacts_every_credential_field(key: str):
    """追踪落库前同样要打码——两份表不能各自漂移。"""
    assert _redact_trace_value({key: "REAL"})[key] == "[redacted]", key


@pytest.mark.parametrize("key", NON_CREDENTIAL_KEYS)
def test_trace_storage_keeps_non_credential_fields(key: str):
    assert _redact_trace_value({key: 1234})[key] == 1234, key
