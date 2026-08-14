from abc import abstractmethod
from enum import Enum


class ModelType(Enum):
    """
    模型类型枚举
    """
    LLM = "llm"
    Embedding = "embedding"
    ImageGeneration = "image_generation"
    Audio = "audio"
    # 可以根据需要添加更多类型
    
    @classmethod
    def from_str(cls, value: str) -> "ModelType":
        """
        从字符串转换为ModelType枚举
        """
        return next(
            (enum_value for enum_value in cls if enum_value.value == value),
            cls.LLM,
        )

class ModelAbility(Enum):
    
    """
    模型能力抽象基类
    """
    @abstractmethod
    def is_capable(self, ability: int) -> bool:
        """
        检查模型是否具备指定能力
        """
        return False
    
    
class LLMAbility(ModelAbility):
    """
    定义了 LLMAbility 的枚举类型，用于表示 LLM 的能力。
    """

    # 这里表示接口支持 chat 格式的对话
    Unknown = 0
    Chat = 1 << 1
    TextInput = 1 << 2
    TextOutput = 1 << 3
    ImageInput = 1 << 4
    ImageOutput = 1 << 5
    AudioInput = 1 << 6
    AudioOutput = 1 << 7
    FunctionCalling = 1 << 8
    # 下面是通过位运算组合能力
    TextCompletion = TextInput | TextOutput
    TextChat = Chat | TextCompletion
    ImageGeneration = ImageInput | ImageOutput
    TextImageMultiModal = Chat | ImageGeneration
    TextImageAudioMultiModal = TextImageMultiModal | AudioInput | AudioOutput
    
    def is_capable(self, ability: int) -> bool:
        """
        检查模型是否具备指定能力
        """
        return (self.value & ability) == ability


class EmbeddingModelAbility(ModelAbility):
    """
    定义了 EmbeddingModelAbility 的枚举类型，用于表示 Embedding 模型的能力。
    """
    Unknown = 0
    TextEmbedding = 1 << 1
    ImageEmbedding = 1 << 2
    AudioEmbedding = 1 << 3
    VideoEmbedding = 1 << 4
    Batch = 1 << 5
    
    def is_capable(self, ability: int) -> bool:
        """
        检查模型是否具备指定能力
        """
        return (self.value & ability) == ability


class ImageModelAbility(ModelAbility):
    """
    定义了 ImageModelAbility 的枚举类型，用于表示图像模型的能力。
    """
    Unknown = 0
    TextToImage = 1 << 1
    ImageEdit = 1 << 2
    Inpainting = 1 << 3
    Outpainting = 1 << 4
    UpScaling = 1 << 5
    
    def is_capable(self, ability: int) -> bool:
        """
        检查模型是否具备指定能力
        """
        return (self.value & ability) == ability    

class AudioModelAbility(ModelAbility):
    """
    定义了 AudioModelAbility 的枚举类型，用于表示音频模型的能力。
    """
    Unknown = 0
    Speech = 1 << 1
    Transcription = 1 << 2
    Translation = 1 << 3
    Streaming = 1 << 4
    Realtime = 1 << 5
    
    def is_capable(self, ability: int) -> bool:
        """
        检查模型是否具备指定能力
        """
        return (self.value & ability) == ability
