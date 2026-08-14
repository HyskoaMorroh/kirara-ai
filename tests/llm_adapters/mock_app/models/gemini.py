from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal, Optional, Union, Any
from typing_extensions import Self
import re

BASE_REGEX = r"^[a-zA-Z0-9_-]+$"

class BatchEmbeddingPart(BaseModel):
    text: str
class BatchEmbeddingParts(BaseModel):
    parts: list[BatchEmbeddingPart]
class BatchEmbeddingPayload(BaseModel):
    model: Literal["mock_embedding"]
    content: BatchEmbeddingParts

class BatchEmbeddingRequest(BaseModel):
    requests: list[BatchEmbeddingPayload]

class Blob(BaseModel):
    mimeType: str
    data: str = Field(description="媒体格式的原始字节。使用 base64 编码的字符串。")

class FunctionCall(BaseModel):
    id: Optional[str] = None
    name: str = Field(description="必需。要调用的函数名称。必须是 a-z、A-Z、0-9 或包含下划线和短划线，长度上限为 63。", max_length=63)
    args: Optional[dict] = None

    @field_validator("name", mode="after")
    @classmethod
    def regex_validator(cls, value: str) -> str:
        if not re.match(BASE_REGEX, value):
            raise ValueError("Invalid function name")
        return value

class FunctionResponse(BaseModel):
    id: Optional[str] = None
    name: str = Field(max_length=63)
    response: dict = Field(description="必需。json 格式的函数调用的返回值。")

    @field_validator("name", mode="after")
    @classmethod
    def regex_validator(cls, value: str) -> str:
        if not re.match(BASE_REGEX, value):
            raise ValueError("Invalid function name")
        return value

class FileData(BaseModel):
    mimeType: Optional[str]
    fileUri: str = Field(description="必需。文件 URI。")

class ExecutableCode(BaseModel):
    language: Literal["PYTHON"] = Field(description="必需。代码语言。")
    code: str = Field(description="必需。要执行代码内容（python支持numpy和simpy库）。")

class CodeExecutionResult(BaseModel):
    outcome: Literal["OUTCOME_OK", "OUTCOME_FAILED", "OUTCOME_DEADLINE_EXCEEDED"]
    output: Optional[str] = None

class Part(BaseModel):
    thought: Optional[bool] = Field(
        default=None, description="可选。指示相应部件是否是从模型中推断出来的。"
    )
    # 下述为 gemini api的联合类型。同一时间只有一个字段
    text: Optional[str] = None
    inlineData: Optional[Blob] = Field(None, validation_alias="inline_data") # 别名不知道是否正确
    functionCall: Optional[FunctionCall] = None
    functionResponse: Optional[FunctionResponse] = None
    fileData: Optional[FileData] = None
    executableCode: Optional[ExecutableCode] = None
    codeExecutionResult: Optional[CodeExecutionResult] = None

    @model_validator(mode="after")
    def validate_mutually_exclusive_fields(self) -> Self:
        # 需要检查互斥的字段列表
        mutually_exclusive_fields = [
            'text', 'inlineData', 'functionCall', 
            'functionResponse', 'fileData', 
            'executableCode', 'codeExecutionResult'
        ]

        # 统计这些字段中有值的数量
        count = sum(1 for field in mutually_exclusive_fields if getattr(self, field) is not None)

        if count > 1:
            raise ValueError("Only one field can be set at a time")
        
        return self

class Content(BaseModel):
    parts: list[Part]
    role: Literal["user", "model"]

class FunctionDeclaration(BaseModel):
    name: str = Field(max_length=63)
    description: str
    parameters: Optional[dict] = None
    response: Optional[dict] = None

    @field_validator("name", mode="after")
    @classmethod
    def regex_validator(cls, value: str) -> str:
        if not re.match(BASE_REGEX, value):
            raise ValueError("Invalid function name")
        return value
class DynamicRetrievalConfig(BaseModel):
    mode: Literal["MODE_DYNAMIC"]
    dynamicThreshold: Optional[float] = None

class GoogleSearchRetrieval(BaseModel):
    dynamicRetrievalConfig: DynamicRetrievalConfig

class Tool(BaseModel):
    functionDeclarations: Optional[list[FunctionDeclaration]]
    googleSearchRetrieval: Optional[GoogleSearchRetrieval]
    codeExecution: Optional[Any] = Field(default=None, description="用于执行模型生成的代码并自动将结果返回给模型的工具。另请参阅 ExecutableCode 和 CodeExecutionResult，它们仅在使用此工具时生成。")
    googleSearch: Optional[Any] = Field(default=None, description="GoogleSearch 工具类型。用于在模型中支持 Google 搜索的工具。由 Google 提供支持。")

class FunctionCallingConfig(BaseModel):
    mode: Literal["AUTO", "ANY", "NONE"]
    allowedFunctionNames: Optional[list[str]] = Field(None, description="可选。一组函数名称。如果提供，则会限制模型将调用的函数。仅当模式为“任意”时，才应设置此字段。函数名称应与 [FunctionDeclaration.name] 相匹配。将模式设置为 ANY 后，模型将从提供的一组函数名称中预测函数调用。")

class ToolConfig(BaseModel):
    functionCallingConfig: Optional[FunctionCallingConfig]

class SafetySettings(BaseModel):
    category: Literal[
        "HARM_CATEGORY_DEROGATORY", 
        "HARM_CATEGORY_TOXICITY", 
        "HARM_CATEGORY_VIOLENCE",
        "HARM_CATEGORY_SEXUAL",
        "HARM_CATEGORY_MEDICAL",
        "HARM_CATEGORY_DANGEROUS",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
        "HARM_CATEGORY_CIVIC_INTEGRITY"
    ]
    threshold: Literal[
        "BLOCK_LOW_AND_ABOVE",
        "BLOCK_MEDIUM_AND_ABOVE",
        "BLOCK_ONLY_HIGH",
        "BLOCK_NONE",
        "OFF"
    ]

class PrebuiltVoiceConfig(BaseModel):
    voiceName: str

class VoiceConfig(BaseModel):
    voice_config: Union[PrebuiltVoiceConfig] # api上这样写的，是联合类型

class SpeechConfig(BaseModel):
    voiceConfig: Optional[VoiceConfig] = None
    languageCode: Optional[str] = None
                         
class GenerationConfig(BaseModel):
    stopSequences: Optional[list[str]] = None
    responseMimeType: Optional[str] = None
    responseSchema: Optional[dict] = None
    # responseModalities: Optional[list[Literal[
    #     "TEXT",
    #     "IMAGE",
    #     "AUDIO",
    # ]]]
    responseModalities: Optional[list[Literal[
        "text",
        "image",
        "audio"
    ]]] = None
    candidateCount: Optional[int] = 1
    maxOutputTokens: Optional[int] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="默认值由具体模型决定")
    topP: Optional[float] = None
    topK: Optional[int] = None
    seed: Optional[int] = None
    presencePenalty: Optional[float] = None
    frequencyPenalty: Optional[float] = None
    responseLogprobs: Optional[bool] = None
    logprobs: Optional[int] = None
    enableEnhancedCivicAnswers: Optional[bool] = None
    speechConfig: Optional[SpeechConfig] = None

    @field_validator("stopSequences", mode="after")
    @classmethod
    def stop_sequences_validator(cls, value: list[str]) -> list[str]:
        if value and len(value) > 5:
            raise ValueError("Stop sequences should not be more than 5")
        return value

class ThinkingConfig(BaseModel):
    includeThoughts: Optional[bool] = None
    thinkingBudget: Optional[int] = None

class ChatRequest(BaseModel):
    contents: list[Content]
    tools: Optional[list[Tool]] = None
    toolConfig: Optional[ToolConfig] = None
    safetySettings: Optional[list[SafetySettings]] = None
    systemInstruction: Optional[Content] = None
    generationConfig: Optional[GenerationConfig] = None
    cachedContent: Optional[str] = None
    thinkingConfig: Optional[ThinkingConfig] = None
    mediaResolution: Optional[Literal[
        "MEDIA_RESOLUTION_LOW",
        "MEDIA_RESOLUTION_MEDIUM",
        "MEDIA_RESOLUTION_HIGH"
    ]] = None