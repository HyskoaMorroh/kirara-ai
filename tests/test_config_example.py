"""`config.yaml.example` 必须能被当前的 `GlobalConfig` 解析。

它是新部署的起点，也是文档里被复制最多的一段。里面写错一个键名或缩进，
症状是「照文档配完启动就报验证失败」——而这类错误在任何单元测试里都不会出现，
因为测试构造的都是 Python 对象而不是这份文件。

新增配置项时如果只改了模型、忘了这份示例，示例并不会失效（多余/缺失的可选键
都能通过），因此这里额外钉住几个**必须在示例里出现**的、行为敏感的开关：
让人知道它们存在，比让它们能被解析更重要。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from kirara_ai.config.global_config import CreatorChannelIdentity, GlobalConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = PROJECT_ROOT / "config.yaml.example"


def _example_document() -> dict:
    yaml = YAML(typ="safe")
    with EXAMPLE_PATH.open("r", encoding="utf-8") as handle:
        document = yaml.load(handle)
    assert isinstance(document, dict), "config.yaml.example 必须是一个 YAML 映射"
    return document


def test_the_example_config_validates_against_the_current_model():
    GlobalConfig.model_validate(_example_document())


def test_the_example_documents_the_behaviour_sensitive_switches():
    """这些开关一旦不出现在示例里，就等于「存在但没人知道」。"""
    text = EXAMPLE_PATH.read_text(encoding="utf-8")
    for key in (
        # 决定聊天侧能否使用 MCP 工具与 command Hook。不写出来的话，
        # 用户只会看到「QQ 里工具永远不可用」而找不到开关在哪。
        "creator_channel_identities",
        # 群聊默认关闭这件事必须在示例里可见，否则开了才发现语义。
        "allow_group_chat",
    ):
        assert key in text, f"config.yaml.example 未提及 {key}"


def test_creator_channel_identities_defaults_to_empty_in_the_example():
    """示例里必须是空表：一份带着真实 QQ 号的示例是一次等待发生的授权错误。"""
    document = _example_document()
    identities = document.get("agent_runtime", {}).get("creator_channel_identities")

    assert identities == [], (
        "示例里的 creator_channel_identities 必须为空表；"
        "写入任何具体身份都会让照抄示例的部署把陌生账号当作创建者"
    )


def test_the_example_contains_no_real_credentials():
    """示例不得带真实凭据；占位值必须一眼可辨。"""
    text = EXAMPLE_PATH.read_text(encoding="utf-8")
    for pattern in ("sk-", "Bearer ", "ghp_"):
        assert pattern not in text, f"config.yaml.example 疑似包含真实凭据：{pattern}"


@pytest.mark.parametrize(
    "payload",
    [
        {"channel_type": "onebot", "sender_scope": ""},
        {"channel_type": "", "sender_scope": "10001"},
    ],
)
def test_an_incomplete_identity_is_rejected_rather_than_matching_everyone(payload):
    """空字段会匹配到 `ChannelContext` 的兜底值，等于对所有人放开。"""
    with pytest.raises(ValueError):
        CreatorChannelIdentity(**payload)
