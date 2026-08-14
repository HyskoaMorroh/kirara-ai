import aiohttp

from kirara_ai.config.global_config import ModelConfig
from kirara_ai.llm.model_types import LLMAbility, ModelType

from .openai_adapter import OpenAIAdapter, OpenAIConfig


class OpenRouterConfig(OpenAIConfig):
    api_base: str = "https://openrouter.ai/api/v1"

class OpenRouterAdapter(OpenAIAdapter):
    def __init__(self, config: OpenRouterConfig):
        super().__init__(config)

    async def auto_detect_models(self) -> list[ModelConfig]:
        all_models: list[ModelConfig] = []
        api_url = f"{self.config.api_base}/models"
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(
                api_url, headers={"Authorization": f"Bearer {self.config.api_key}"}
            ) as response:
                response.raise_for_status()
                response_data = await response.json()
                for model in response_data.get("data", []):
                    ability = LLMAbility.TextChat.value
                    for input_modality in model["architecture"]["input_modalities"]:
                        if input_modality == "text":
                            ability |= LLMAbility.TextInput.value
                        elif input_modality == "image":
                            ability |= LLMAbility.ImageInput.value

                    for output_modality in model["architecture"]["output_modalities"]:
                        if output_modality == "text":
                            ability |= LLMAbility.TextOutput.value
                        elif output_modality == "image":
                            ability |= LLMAbility.ImageOutput.value

                    all_models.append(ModelConfig(id=model["id"], type=ModelType.LLM.value, ability=ability))
                return all_models