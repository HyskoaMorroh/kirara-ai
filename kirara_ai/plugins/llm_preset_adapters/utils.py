import uuid
from typing import Optional, Tuple

from kirara_ai.llm.format.message import LLMChatContentPartType, LLMToolCallContent
from kirara_ai.llm.format.tool import Function, ToolCall
from kirara_ai.llm.model_types import AudioModelAbility, EmbeddingModelAbility, ImageModelAbility, LLMAbility, ModelType


def generate_tool_call_id(name: str) -> str:
    return f"{name}_{str(uuid.uuid4())}"

def pick_tool_calls(calls: list[LLMChatContentPartType]) -> Optional[list[ToolCall]]:
    tool_calls = [
        ToolCall(
            id=call.id,
            function=Function(name=call.name, arguments=call.parameters)
        ) for call in calls if isinstance(call, LLMToolCallContent)
    ]
    if tool_calls:
        return tool_calls
    else:
        return None

def guess_openai_model(model_id: str) -> Tuple[ModelType, int] | None:
    """
    根据模型ID猜测模型类型和能力
    返回: (ModelType, ability_bitmask) 或 None
    """
    model_id = model_id.lower()

    # 1. 检查嵌入模型
    if "embedding" in model_id:
        return (ModelType.Embedding, EmbeddingModelAbility.TextEmbedding.value | EmbeddingModelAbility.Batch.value)  # 嵌入模型

    # 2. 检查图像生成模型
    if "dall-e" in model_id or "gpt-image" in model_id:
        ability = ImageModelAbility.TextToImage.value
        if "dall-e-2" in model_id or "gpt-image" in model_id:
            ability |= ImageModelAbility.ImageEdit.value | ImageModelAbility.Inpainting.value
        return (ModelType.ImageGeneration, ability)

    # 3. 检查音频模型
    if "whisper" in model_id:
        return (ModelType.Audio, AudioModelAbility.Transcription.value | AudioModelAbility.Translation.value)
    if "tts" in model_id:
        if "mini" in model_id:
            return (ModelType.Audio, AudioModelAbility.Speech.value | AudioModelAbility.Streaming.value)
        return (ModelType.Audio, AudioModelAbility.Speech.value)
    if model_id == "gpt-4o-transcribe":
        # 特别处理 gpt-4o-transcribe，没有 Translation 能力
        return (ModelType.Audio, AudioModelAbility.Transcription.value | AudioModelAbility.Streaming.value | AudioModelAbility.Realtime.value)
    if "transcribe" in model_id:
        if "mini" in model_id:
            return (ModelType.Audio, AudioModelAbility.Transcription.value | AudioModelAbility.Realtime.value)
        if "realtime" in model_id:
            return (ModelType.Audio, AudioModelAbility.Transcription.value | AudioModelAbility.Realtime.value)
        else:
            return (ModelType.Audio, AudioModelAbility.Transcription.value | AudioModelAbility.Translation.value | AudioModelAbility.Streaming.value | AudioModelAbility.Realtime.value)

    # 4. 处理音频相关的LLM模型 (这些不应该有图像输入能力)
    if ("audio" in model_id or "realtime" in model_id) and "4o" in model_id:
        ability = LLMAbility.TextChat.value | LLMAbility.AudioInput.value | LLMAbility.AudioOutput.value

        # 特殊情况处理
        if "gpt-4o-mini-audio-preview-2024-12-17" in model_id:
            ability |= LLMAbility.FunctionCalling.value
            return (ModelType.LLM, ability)

        if "gpt-4o-mini-realtime-preview-2024-12-17" in model_id:
            ability |= LLMAbility.FunctionCalling.value
            return (ModelType.LLM, ability)

        if not ("mini-search" in model_id or "mini-realtime" in model_id or
                "instruct" in model_id or "search" in model_id or
                "mini" in model_id):
            ability |= LLMAbility.FunctionCalling.value
        return (ModelType.LLM, ability)
    # 5. 检查moderation模型
    if "moderation" in model_id:
        return None
    # 6. LLM模型 (默认情况)
    ability = LLMAbility.TextChat.value
    if ("babbage" in model_id or "davinci" in model_id):
        return (ModelType.LLM, LLMAbility.TextInput.value | LLMAbility.TextOutput.value)

    # 图像输入能力
    if ("vision" in model_id or
        "4o" in model_id or
        "computer-use-preview" in model_id or
        "gpt-4-turbo" in model_id or
        "o1" in model_id or
        "o4" in model_id or
        "4." in model_id or
        ("4" in model_id and ("image" in model_id or "vision" in model_id))):
        ability |= LLMAbility.ImageInput.value

    # 大部分模型都应该有函数调用能力，除了特定例外
    if not ("3.5" in model_id or
            "3-5" in model_id or
            "1106" in model_id or
            "0314" in model_id or
            "0125" in model_id or
            "gpt-4-0" in model_id or
            "chatgpt-4o" in model_id or
            "instruct" in model_id or
            "search" in model_id or
            "o1-mini" in model_id or
            model_id.startswith(("babbage", "davinci"))):
        ability |= LLMAbility.FunctionCalling.value

    # 特殊模型处理
    if "o3-2025-04-16" in model_id:
        ability = LLMAbility.TextChat.value | LLMAbility.ImageInput.value | LLMAbility.FunctionCalling.value

    if "o1-preview-2024-09-12" in model_id:
        ability = LLMAbility.TextChat.value | LLMAbility.FunctionCalling.value

    if "o1-mini-2024-09-12" in model_id:
        ability = LLMAbility.TextChat.value

    if "o3-mini-2025-01-31" in model_id:
        ability = LLMAbility.TextChat.value | LLMAbility.FunctionCalling.value

    return (ModelType.LLM, ability)

def guess_qwen_model(model_id: str) -> Tuple[ModelType, int] | None:
    """
    根据模型ID猜测通义千问模型的类型和能力
    返回: (ModelType, ability_bitmask) 或 None
    """
    model_id = model_id.lower()

    # 通义千问Embedding模型
    if "text-embedding" in model_id:
        return (ModelType.Embedding, EmbeddingModelAbility.TextEmbedding.value | EmbeddingModelAbility.Batch.value)

    if "multimodal-embedding-v1" in model_id:
        return (ModelType.Embedding, EmbeddingModelAbility.TextEmbedding.value | EmbeddingModelAbility.ImageEmbedding.value | EmbeddingModelAbility.AudioEmbedding.value | EmbeddingModelAbility.VideoEmbedding.value | EmbeddingModelAbility.Batch.value)

    # 通义千问多模态模型
    if "-vl" in model_id or "qvq-":
        return (ModelType.LLM, LLMAbility.TextChat.value | LLMAbility.ImageInput.value)

    if "-audio" in model_id:
        return (ModelType.LLM, LLMAbility.TextChat.value | LLMAbility.AudioInput.value)

    if "qwen-omni" in model_id:
        return (ModelType.LLM, LLMAbility.TextChat.value | LLMAbility.ImageInput.value | LLMAbility.AudioInput.value | LLMAbility.AudioOutput.value)

    # 通义千问系列基础模型
    if "qwen" in model_id:
        return (ModelType.LLM, LLMAbility.TextChat.value | LLMAbility.FunctionCalling.value)

    return None
