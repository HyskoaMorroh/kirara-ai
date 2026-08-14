
from llm_preset_adapters.utils import guess_openai_model

from kirara_ai.llm.model_types import AudioModelAbility, EmbeddingModelAbility, ImageModelAbility, LLMAbility, ModelType
from kirara_ai.logger import get_logger

models = [
    {
        "id": "babbage-002",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextCompletion.value
    },
    {
        "id": "chatgpt-4o-latest",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value
    },
    {
        "id": "computer-use-preview-2025-03-11",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value | LLMAbility.FunctionCalling.value
    },
    {
        "id": "dall-e-2",
        "type": ModelType.ImageGeneration.value,
        "abilities": ImageModelAbility.TextToImage.value | ImageModelAbility.ImageEdit.value | ImageModelAbility.Inpainting.value
    },
    {
        "id": "dall-e-3",
        "type": ModelType.ImageGeneration.value,
        "abilities": ImageModelAbility.TextToImage.value
    },
    {
        "id": "davinci-002",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextCompletion.value
    },
    {
        "id": "gpt-3-5-0301",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value
    },
    {
        "id": "gpt-3-5-turbo-0125",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value
    },
    {
        "id": "gpt-3-5-turbo-0613",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value
    },
    {
        "id": "gpt-3-5-turbo-1106",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value
    },
    {
        "id": "gpt-3-5-turbo-16k-0613",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value
    },
    {
        "id": "gpt-3-5-turbo-instruct",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value
    },
    {
        "id": "gpt-4-0125-preview",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value
    },
    {
        "id": "gpt-4-0314",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value
    },
    {
        "id": "gpt-4-0613",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value
    },
    {
        "id": "gpt-4-turbo-2024-04-09",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value | LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4.1-2025-04-14",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value | LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4.1-mini-2025-04-14",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value |
        LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4.1-nano-2025-04-14",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value |
        LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4.5-preview-2025-02-27",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value | LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4o-2024-05-13",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value |
        LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4o-2024-08-06",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value | LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4o-2024-11-20",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value | LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4o-audio-preview-2024-10-01",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.AudioInput.value | LLMAbility.AudioOutput.value | LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4o-audio-preview-2024-12-17",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.AudioInput.value | LLMAbility.AudioOutput.value | LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4o-mini-2024-07-18",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value |
        LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4o-mini-audio-preview-2024-12-17",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.AudioInput.value | LLMAbility.AudioOutput.value | LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4o-mini-realtime-preview-2024-12-17",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.AudioInput.value | LLMAbility.AudioOutput.value |
        LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4o-mini-search-preview-2025-03-11",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value
    },
    {
        "id": "gpt-4o-mini-transcribe",
        "type": ModelType.Audio.value,
        "abilities": AudioModelAbility.Transcription.value | AudioModelAbility.Realtime.value
    },
    {
        "id": "gpt-4o-mini-tts",
        "type": ModelType.Audio.value,
        "abilities": AudioModelAbility.Speech.value | AudioModelAbility.Streaming.value
    },
    {
        "id": "gpt-4o-realtime-preview-2024-10-01",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.AudioInput.value | LLMAbility.AudioOutput.value |
        LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4o-realtime-preview-2024-12-17",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.AudioInput.value | LLMAbility.AudioOutput.value |
        LLMAbility.FunctionCalling.value
    },
    {
        "id": "gpt-4o-search-preview-2025-03-11",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value
    },
    {
        "id": "gpt-4o-transcribe",
        "type": ModelType.Audio.value,
        "abilities": AudioModelAbility.Transcription.value | AudioModelAbility.Streaming.value | AudioModelAbility.Realtime.value
    },
    {
        "id": "gpt-image-1",
        "type": ModelType.ImageGeneration.value,
        "abilities": ImageModelAbility.TextToImage.value | ImageModelAbility.ImageEdit.value |
        ImageModelAbility.Inpainting.value
    },
    {
        "id": "o1-2024-12-17",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value |
        LLMAbility.FunctionCalling.value
    },
    {
        "id": "o1-mini-2024-09-12",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value
    },
    {
        "id": "o1-preview-2024-09-12",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.FunctionCalling.value
    },
    {
        "id": "o1-pro-2025-03-19",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value |
        LLMAbility.FunctionCalling.value
    },
    {
        "id": "o3-2025-04-16",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value |
        LLMAbility.FunctionCalling.value
    },
    {
        "id": "o3-mini-2025-01-31",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value |
        LLMAbility.FunctionCalling.value
    },
    {
        "id": "o4-mini-2025-04-16",
        "type": ModelType.LLM.value,
        "abilities": LLMAbility.TextChat.value | LLMAbility.ImageInput.value | LLMAbility.FunctionCalling.value
    },
    {
        "id": "text-embedding-3-large",
        "type": ModelType.Embedding.value,
        "abilities": EmbeddingModelAbility.TextEmbedding.value | EmbeddingModelAbility.Batch.value
    },
    {
        "id": "text-embedding-3-small",
        "type": ModelType.Embedding.value,
        "abilities": EmbeddingModelAbility.TextEmbedding.value | EmbeddingModelAbility.Batch.value
    },
    {
        "id": "text-embedding-ada-002",
        "type": ModelType.Embedding.value,
        "abilities": EmbeddingModelAbility.TextEmbedding.value | EmbeddingModelAbility.Batch.value
    },
    {
        "id": "tts-1-hd",
        "type": ModelType.Audio.value,
        "abilities": AudioModelAbility.Speech.value
    },
    {
        "id": "tts-1",
        "type": ModelType.Audio.value,
        "abilities": AudioModelAbility.Speech.value
    },
    {
        "id": "whisper-1",
        "type": ModelType.Audio.value,
        "abilities": AudioModelAbility.Transcription.value | AudioModelAbility.Translation.value
    }
]

def test_guess_openai_model():
    logger = get_logger("test_guess_openai_model")
    failed_tests = []
    
    for model in models:
        model_type, abilities = guess_openai_model(model["id"])
        logger.info(f"模型: {model['id']}, 模型类型: {model_type}, 能力: {abilities}, 预期: {model['type']}, {model['abilities']}")
        
        try:
            assert model_type is not None, f"模型 {model['id']} 的类型不应为 None"
            assert model_type.value == model["type"], f"模型 {model['id']} 的类型不匹配，预期 {model['type']}，实际 {model_type.value}"
            
            if abilities != model["abilities"]:
                diff_bits = abilities ^ model["abilities"]
                expected_but_missing = diff_bits & model["abilities"]
                unexpected_but_present = diff_bits & abilities
                
                error_msg = f"模型 {model['id']} 的能力不匹配，预期 {model['abilities']}，实际 {abilities}"
                
                # 根据模型类型获取对应的能力枚举类
                ability_enum = None
                if model_type == ModelType.LLM:
                    ability_enum = LLMAbility
                elif model_type == ModelType.Embedding:
                    ability_enum = EmbeddingModelAbility
                elif model_type == ModelType.ImageGeneration:
                    ability_enum = ImageModelAbility
                elif model_type == ModelType.Audio:
                    ability_enum = AudioModelAbility
                
                # 获取缺少的能力名称
                if expected_but_missing:
                    missing_abilities = []
                    for enum_item in ability_enum:
                        if expected_but_missing & enum_item.value:
                            missing_abilities.append(enum_item.name)
                    error_msg += f"，缺少能力: {', '.join(missing_abilities)} ({bin(expected_but_missing)})"
                
                # 获取多余的能力名称
                if unexpected_but_present:
                    extra_abilities = []
                    for enum_item in ability_enum:
                        if unexpected_but_present & enum_item.value:
                            extra_abilities.append(enum_item.name)
                    error_msg += f"，多余能力: {', '.join(extra_abilities)} ({bin(unexpected_but_present)})"
                
                assert False, error_msg
                
        except AssertionError as e:
            failed_tests.append(str(e))
            logger.error(str(e))
    
    if failed_tests:
        error_message = f"共有 {len(failed_tests)} 个测试点失败:\n" + "\n".join(failed_tests)
        assert False, error_message
