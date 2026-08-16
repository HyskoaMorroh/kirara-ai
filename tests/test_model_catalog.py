from kirara_ai.config.global_config import ModelConfig
from kirara_ai.llm.model_types import LLMAbility
from kirara_ai.scheduler.model_catalog import model_catalogs_equal, normalize_detected_models


def test_detected_catalog_replaces_stale_entries_and_keeps_provider_order():
    existing = [
        ModelConfig(id="legacy-primary", type="llm", ability=1),
        ModelConfig(id="current", type="llm", ability=1),
    ]
    detected = [
        ModelConfig(id="new-primary", type="llm", ability=1),
        ModelConfig(id="current", type="llm", ability=1),
        ModelConfig(id="new-primary", type="llm", ability=3),
    ]

    refreshed = normalize_detected_models(detected)

    assert [model.id for model in refreshed] == ["new-primary", "current"]
    assert refreshed[0].ability == 3
    assert not model_catalogs_equal(existing, refreshed)


def test_catalog_comparison_detects_metadata_changes_for_the_same_model_id():
    existing = [ModelConfig(id="chat", type="llm", ability=1)]
    detected = [ModelConfig(id="chat", type="llm", ability=3)]

    refreshed = normalize_detected_models(detected)

    assert not model_catalogs_equal(existing, refreshed)


def test_catalog_comparison_reports_equality_for_an_unchanged_catalog():
    """相等分支必须真的返回 True。

    原先两条用例都只断言「不相等」，一个永远返回 False 的实现也能全部通过；
    而 model_catalogs_equal 恒为 False 会让调度器每轮都误判成「模型列表变了」，
    从而不断重载后端并重写 config.yaml。
    """
    existing = [
        ModelConfig(id="chat", type="llm", ability=3),
        ModelConfig(id="vision", type="llm", ability=1),
    ]
    detected = [
        ModelConfig(id="chat", type="llm", ability=3),
        ModelConfig(id="vision", type="llm", ability=1),
    ]

    refreshed = normalize_detected_models(detected)

    assert model_catalogs_equal(existing, refreshed)
    # 同一份输入重复归一化的结果必须稳定，否则下拉框顺序会来回抖动
    assert model_catalogs_equal(refreshed, normalize_detected_models(detected))


def test_catalog_comparison_is_order_sensitive_but_order_is_deterministic():
    """顺序不同视为不同（会写回配置），但同一次检测结果的顺序是确定的。"""
    first = normalize_detected_models(
        [ModelConfig(id="a", type="llm", ability=1), ModelConfig(id="b", type="llm", ability=1)]
    )
    second = normalize_detected_models(
        [ModelConfig(id="b", type="llm", ability=1), ModelConfig(id="a", type="llm", ability=1)]
    )

    assert [model.id for model in first] == ["a", "b"]
    assert [model.id for model in second] == ["b", "a"]
    assert not model_catalogs_equal(first, second)


def test_plain_string_models_are_normalized_into_text_chat_models():
    """适配器只返回模型名字符串时，也要补齐类型与能力，且结果可判等。"""
    refreshed = normalize_detected_models(["gpt-mini", "gpt-mini"])

    assert [model.id for model in refreshed] == ["gpt-mini"]
    assert refreshed[0].type == "llm"
    assert refreshed[0].ability == LLMAbility.TextChat.value
    assert model_catalogs_equal(refreshed, normalize_detected_models(["gpt-mini"]))
