import hashlib
import json
from collections.abc import Iterable
from typing import Any

from kirara_ai.config.global_config import LLMBackendConfig, ModelConfig
from kirara_ai.llm.model_types import LLMAbility, ModelType


def normalize_detected_models(models: Iterable[ModelConfig | str]) -> list[ModelConfig]:
    """Convert an adapter's latest discovery result into a stable model catalogue.

    The provider's ordering is retained so its preferred/latest ordering reaches the
    WebUI. Duplicate IDs retain their first position while the last metadata record
    wins, which keeps pagination duplicates from creating multiple choices.

    关于「下拉框顺序会不会跟着变」：不会。工作流节点里的模型下拉框由
    `model_name_options_provider` 提供，它对结果做了 `sorted()`，因此展示顺序
    始终按 ID 升序稳定。这里保留提供方顺序只影响 config.yaml 里的写入次序，
    对用户可见的选择列表没有影响；同一次检测的结果是确定性的，不会来回抖动。
    """
    model_by_id: dict[str, ModelConfig] = {}
    model_ids: list[str] = []

    for model in models:
        model_config = (
            model
            if isinstance(model, ModelConfig)
            else ModelConfig(
                id=str(model),
                type=ModelType.LLM.value,
                ability=LLMAbility.TextChat.value,
            )
        )
        # 保持原自动检测的兼容行为：适配器只知道“这是 LLM”但未声明能力时，
        # 将它作为可文本对话模型展示。使用副本，避免修改适配器返回的对象。
        if model_config.type == ModelType.LLM.value and not model_config.ability:
            model_config = model_config.model_copy(
                update={"ability": LLMAbility.TextChat.value}
            )
        if model_config.id not in model_by_id:
            model_ids.append(model_config.id)
        model_by_id[model_config.id] = model_config

    return [model_by_id[model_id] for model_id in model_ids]


def model_catalogs_equal(existing: Iterable[ModelConfig], detected: Iterable[ModelConfig]) -> bool:
    """Compare complete model metadata instead of comparing only model IDs."""
    return [model.model_dump(mode="json") for model in existing] == [
        model.model_dump(mode="json") for model in detected
    ]


def backend_config_fingerprint(config: LLMBackendConfig | Any) -> str:
    """Fingerprint backend identity and settings while excluding its catalogue."""
    if hasattr(config, "model_dump"):
        payload = config.model_dump(mode="json", exclude={"models"})
    else:
        payload = {
            key: value
            for key, value in vars(config).items()
            if key != "models"
        }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
