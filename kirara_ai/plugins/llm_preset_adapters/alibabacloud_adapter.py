from kirara_ai.config.global_config import ModelConfig

from .openai_adapter import OpenAIAdapter, OpenAIConfig
from .utils import guess_qwen_model


class AlibabaCloudConfig(OpenAIConfig):
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class AlibabaCloudAdapter(OpenAIAdapter):
    def __init__(self, config: AlibabaCloudConfig):
        super().__init__(config)

    async def auto_detect_models(self) -> list[ModelConfig]:
        models = await self.get_models()
        all_models: list[ModelConfig] = []
        for model in models:
            guess_result = guess_qwen_model(model)
            if guess_result is None:
                continue
            all_models.append(ModelConfig(id=model, type=guess_result[0].value, ability=guess_result[1]))
        return all_models