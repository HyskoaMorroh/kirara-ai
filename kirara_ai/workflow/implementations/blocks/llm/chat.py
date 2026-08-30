import asyncio
import re
import threading
import time
from datetime import datetime
from typing import Annotated, Any, Dict, List, Optional
from abc import ABC, abstractmethod


from kirara_ai.im.message import ImageMessage, IMMessage, MessageElement, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.message import LLMChatContentPartType, LLMChatImageContent
from kirara_ai.llm.format.request import LLMChatRequest, Tool
from kirara_ai.llm.format.response import LLMChatResponse
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.model_types import LLMAbility, ModelType
from kirara_ai.llm.resilience import FailoverExecutionError, RETRYABLE_ERROR_CATEGORIES
from kirara_ai.logger import get_logger
from kirara_ai.memory.composes.base import ComposableMessageType
from kirara_ai.workflow.core.block import Block, Input, Output, ParamMeta
from kirara_ai.workflow.core.execution.executor import WorkflowExecutor


def model_name_options_provider(container: DependencyContainer, block: Block) -> List[str]:
    llm_manager: LLMManager = container.resolve(LLMManager)
    return sorted(llm_manager.get_supported_models(ModelType.LLM, LLMAbility.TextChat))


# 采样温度的合法区间。各家 API 的上限不完全一致（OpenAI 为 2.0，Claude 为 1.0），
# 这里取并集的上限，超出范围的值一律忽略并落回模型自身的默认温度，
# 避免把一个必然被服务端拒绝的请求发出去。
TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 2.0
MODEL_FALLBACK_ERROR_CATEGORIES = {
    *(category.value for category in RETRYABLE_ERROR_CATEGORIES),
    "circuit_open",
}


def _uses_resilient_provider_queue(llm_manager: LLMManager) -> bool:
    return (
        getattr(llm_manager, "_resilience_initialized", False) is True
        and callable(getattr(llm_manager, "execute_chat", None))
    )


def _execute_resilient_chat(
    llm_manager: LLMManager,
    request: LLMChatRequest,
    *,
    max_attempts: Optional[int] = None,
    retry_delay: Optional[float] = None,
    cancellation_event: Optional[threading.Event] = None,
    deadline_seconds: Optional[float] = None,
) -> LLMChatResponse:
    options: Dict[str, Any] = {}
    if max_attempts is not None:
        options["max_retries"] = max(0, max_attempts - 1)
    if retry_delay is not None:
        options["retry_delay"] = retry_delay
    # 取消信号与总截止时间同 Agent 路径一致地下传。遗留工作流路径此前完全不传，
    # 于是一个卡住的上游在这条路径上同样会占着线程与连接直到进程退出。
    if cancellation_event is not None:
        options["cancellation_event"] = cancellation_event
    if deadline_seconds is not None:
        options["deadline_seconds"] = deadline_seconds
    return llm_manager.execute_chat(request, **options).response


def _turn_budget(container: DependencyContainer) -> tuple[Optional[threading.Event], Optional[float]]:
    """Resolve this turn's cancellation signal and remaining time budget.

    与 Agent 路径共用同一个配置项 ``agent_runtime.turn_deadline_seconds``：
    「一轮对话最多花多久」不该因为走的是遗留工作流而变成另一套语义。
    未配置（0）时返回 ``(None, None)``，调用方就什么都不传，行为与从前一致。
    """
    try:
        from kirara_ai.config.global_config import GlobalConfig

        if not container.has(GlobalConfig):
            return None, None
        budget = float(
            getattr(
                getattr(container.resolve(GlobalConfig), "agent_runtime", None),
                "turn_deadline_seconds",
                0.0,
            )
            or 0.0
        )
    except Exception:  # noqa: BLE001 - 读配置失败时退回「无预算」，不影响回复
        return None, None
    if budget <= 0:
        return None, None
    return threading.Event(), budget


def _can_fallback_to_next_model(error: FailoverExecutionError) -> bool:
    if not error.attempts:
        return True
    return error.attempts[-1].error_category in MODEL_FALLBACK_ERROR_CATEGORIES


def resolve_temperature(
    container: DependencyContainer,
    configured_temperature: Optional[float],
    logger=None,
) -> Optional[float]:
    """确定本次请求实际使用的采样温度。

    优先级：节点上显式配置的 temperature > 命中的调度规则 metadata.temperature >
    不传（由模型/后端自己的默认值决定）。这样既能在 WebUI 的节点配置里按工作流
    调温，也让 `data/dispatch_rules/rules.yaml` 里的 metadata.temperature 真正生效。

    :param container: 当前作用域容器，调度器会在其中注册命中的规则
    :param configured_temperature: 节点配置里填写的温度，未填写时为 None
    :param logger: 可选的日志器，用于提示非法取值
    :return: 合法的温度值，或 None 表示不在请求里携带该字段
    """

    def _validate(value: Any, source: str) -> Optional[float]:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            if logger:
                logger.warning(f"Ignoring non-numeric temperature from {source}: {value!r}")
            return None
        if not TEMPERATURE_MIN <= number <= TEMPERATURE_MAX:
            if logger:
                logger.warning(
                    f"Ignoring out-of-range temperature from {source}: {number} "
                    f"(expected {TEMPERATURE_MIN}~{TEMPERATURE_MAX})"
                )
            return None
        return number

    resolved = _validate(configured_temperature, "block config")
    if resolved is not None:
        return resolved

    # 延迟导入：dispatch 包会反向引用工作流执行器，模块级导入会形成循环依赖。
    try:
        from kirara_ai.workflow.core.dispatch.rules.base import DispatchRule
    except Exception:  # pragma: no cover - 仅在包结构异常时触发
        return None

    if not container.has(DispatchRule):
        return None
    rule = container.resolve(DispatchRule)
    metadata = getattr(rule, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    return _validate(metadata.get("temperature"), "dispatch rule metadata")

class ChatMessageConstructor(Block):
    name = "chat_message_constructor"
    description = "把系统提示词、历史记忆与本轮消息组装成模型可接受的对话记录。提示词中可使用 {user_msg}、{user_name}、{memory_content}、{current_date_time} 等占位符。"
    inputs = {
        "user_msg": Input("user_msg", "本轮消息", IMMessage, "用户本轮发送的消息"),
        "user_prompt_format": Input(
            "user_prompt_format", "本轮消息格式", str, "本轮消息的模板，支持占位符", default=""
        ),
        "memory_content": Input("memory_content", "历史消息对话", List[ComposableMessageType], "历史对话记录，通常来自「查询记忆」"),
        "system_prompt_format": Input(
            "system_prompt_format", "系统提示词", str, "系统提示词模板，支持占位符", default=""
        ),
    }
    outputs = {
        "llm_msg": Output(
            "llm_msg", "LLM 对话记录", List[LLMChatMessage], "组装完成的对话记录"
        )
    }
    container: DependencyContainer

    def substitute_variables(self, text: str, executor: WorkflowExecutor) -> str:
        """
        替换文本中的变量占位符，支持对象属性和字典键的访问

        :param text: 包含变量占位符的文本，格式为 {variable_name} 或 {variable_name.attribute}
        :param executor: 工作流执行器实例
        :return: 替换后的文本
        """

        def replace_var(match):
            var_path = match.group(1).split(".")
            var_name = var_path[0]

            # 获取基础变量
            value = executor.get_variable(var_name, match.group(0))

            # 如果有属性/键访问
            for attr in var_path[1:]:
                try:
                    # 尝试字典键访问
                    if isinstance(value, dict):
                        value = value.get(attr, match.group(0))
                    # 尝试对象属性访问
                    elif hasattr(value, attr):
                        value = getattr(value, attr)
                    else:
                        # 如果无法访问，返回原始占位符
                        return match.group(0)
                except Exception:
                    # 任何异常都返回原始占位符
                    return match.group(0)

            return str(value)

        return re.sub(r"\{([^}]+)\}", replace_var, text)

    def execute(
        self,
        user_msg: IMMessage,
        memory_content: str,
        system_prompt_format: str = "",
        user_prompt_format: str = "",
    ) -> Dict[str, Any]:
        # 获取当前执行器
        executor = self.container.resolve(WorkflowExecutor)

        # 先替换自有的两个变量
        replacements = {
            "{current_date_time}": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "{user_msg}": user_msg.content,
            "{user_name}": user_msg.sender.display_name,
            "{user_id}": user_msg.sender.user_id
        }
        
        if isinstance(memory_content, list) and all(isinstance(item, str) for item in memory_content):
            replacements["{memory_content}"] = "\n".join(memory_content)

        for old, new in replacements.items():
            system_prompt_format = system_prompt_format.replace(old, new)
            user_prompt_format = user_prompt_format.replace(old, new)

        # 再替换其他变量
        system_prompt = self.substitute_variables(system_prompt_format, executor)
        user_prompt = self.substitute_variables(user_prompt_format, executor)

        content: List[LLMChatContentPartType] = [LLMChatTextContent(text=user_prompt)]
        # 添加图片内容
        for image in user_msg.images or []:
            content.append(LLMChatImageContent(media_id=image.media_id))

        llm_msg = [
            LLMChatMessage(role="system", content=[LLMChatTextContent(text=system_prompt)]),
        ]
        
        if isinstance(memory_content, list) and all(isinstance(item, LLMChatMessage) for item in memory_content):
            llm_msg.extend(memory_content) # type: ignore
            
        llm_msg.append(LLMChatMessage(role="user", content=content))
        return {"llm_msg": llm_msg}


class ChatCompletion(Block):
    name = "chat_completion"
    description = "调用大语言模型生成回复。可配置最多 5 个模型，前一个失败时自动降级到下一个。"
    inputs = {
        "prompt": Input("prompt", "LLM 对话记录", List[LLMChatMessage], "要发送给模型的对话记录")
    }
    outputs = {"resp": Output("resp", "LLM 对话响应", LLMChatResponse, "模型返回的回复")}
    container: DependencyContainer

    def __init__(
        self,
        model_name: Annotated[
            Optional[str],
            ParamMeta(label="模型 ID1", description="主模型 ID", options_provider=model_name_options_provider),
        ] = None,
        fallback_model_1: Annotated[
            Optional[str],
            ParamMeta(label="模型 ID2", description="备用模型 ID2", options_provider=model_name_options_provider),
        ] = None,
        fallback_model_2: Annotated[
            Optional[str],
            ParamMeta(label="模型 ID3", description="备用模型 ID3", options_provider=model_name_options_provider),
        ] = None,
        fallback_model_3: Annotated[
            Optional[str],
            ParamMeta(label="模型 ID4", description="备用模型 ID4", options_provider=model_name_options_provider),
        ] = None,
        fallback_model_4: Annotated[
            Optional[str],
            ParamMeta(label="模型 ID5", description="备用模型 ID5", options_provider=model_name_options_provider),
        ] = None,
        max_retries: Annotated[
            int,
            ParamMeta(label="最大重试次数", description="每个模型的最大重试次数"),
        ] = 3,
        retry_delay: Annotated[
            float,
            ParamMeta(label="重试延迟(秒)", description="每次重试之间的等待时间"),
        ] = 3.0,
        use_deployment_default_model: Annotated[
            bool,
            ParamMeta(
                label="允许回退到部署默认模型",
                description="开启后，上面配置的模型全部不可用时，会再尝试本机默认的文本对话模型。关闭时只使用上面配置的模型，避免用意料之外的模型回答",
            ),
        ] = False,
        temperature: Annotated[
            Optional[float],
            ParamMeta(
                label="采样温度",
                description="取值 0.0~2.0，越大回答越随机、越小越稳定。留空则使用命中的调度规则 metadata.temperature，两者都没有时交由模型自身默认值决定",
            ),
        ] = None,
    ):
        self.model_name = model_name
        # 将4个备用模型参数组合成列表，过滤掉空值
        fallback_list = [fallback_model_1, fallback_model_2, fallback_model_3, fallback_model_4]
        self.fallback_models = [m for m in fallback_list if m] or None
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        # 是否允许在配置的模型链之后追加本机默认模型。默认关闭：
        # 静默换成另一个模型会让用户得到与预期不符的回答，且难以察觉。
        # 一个模型都没配置时仍然会使用默认模型（否则工作流根本跑不起来）。
        self.use_deployment_default_model = use_deployment_default_model
        # 采样温度：节点上没填时会回落到调度规则的 metadata.temperature。
        self.temperature = temperature
        self.logger = get_logger("ChatCompletionBlock")

    def execute(self, prompt: List[LLMChatMessage]) -> Dict[str, Any]:
        llm_manager = self.container.resolve(LLMManager)

        # 解析本次实际生效的采样温度（节点配置 > 调度规则 metadata > 不携带）
        temperature = resolve_temperature(self.container, self.temperature, self.logger)

        # 构建模型优先级列表
        model_priority_list = []
        if self.model_name:
            model_priority_list.append(self.model_name)

        # 添加备用模型列表，去除与主模型及彼此之间的重复项
        if self.fallback_models:
            for fallback_model in self.fallback_models:
                if fallback_model and fallback_model not in model_priority_list:
                    model_priority_list.append(fallback_model)

        # Keep an explicitly configured model chain authoritative, but add the
        # deployment's compatible default as its final safety net.  Bundled
        # workflows can therefore keep their intended primary/fallback order
        # while a fresh installation still works when those sample IDs are not
        # configured locally.
        # 该兜底现在是显式开关：只有「一个模型都没配置」或用户打开
        # use_deployment_default_model 时才追加默认模型，避免在用户明确指定了
        # 模型链的情况下静默换用另一个模型作答。
        has_configured_model = bool(self.model_name or self.fallback_models)
        if not has_configured_model or self.use_deployment_default_model:
            default_model = llm_manager.get_llm_id_by_ability(LLMAbility.TextChat)
            if default_model and default_model not in model_priority_list:
                model_priority_list.append(default_model)
                if has_configured_model:
                    self.logger.info(
                        f"Adding deployment default model after configured fallbacks: {default_model}"
                    )
                else:
                    self.logger.info(f"Model id unspecified, using default model: {default_model}")

        if not model_priority_list:
            # 报错里带上节点身份：预设工作流的 model_name 故意留空，用户需要知道
            # 该去画布上的哪个节点点开配置。英文原文保留在括号里，便于检索既有日志。
            raise ValueError(
                f"节点「{self.name}」没有可用的模型：请在工作流编辑器中打开该节点，"
                f"从「模型 ID1」下拉框里选择一个已配置的模型"
                f"（No available LLM models found）"
            )

        # 记录模型优先级列表
        self.logger.info(f"Model priority list: {model_priority_list}")

        # 尝试每个模型
        last_error = None
        use_resilient_queue = _uses_resilient_provider_queue(llm_manager)
        # 整轮共享一个取消信号与一个递减预算：模型回退会多次调用上游，
        # 每次都从头给满预算等于没有总预算。
        turn_cancellation, turn_budget = _turn_budget(self.container)
        turn_started = time.monotonic()

        def remaining() -> Optional[float]:
            if turn_budget is None:
                return None
            left = turn_budget - (time.monotonic() - turn_started)
            if left <= 0:
                if turn_cancellation is not None:
                    turn_cancellation.set()
                return 0.0
            return left

        for model_index, model_id in enumerate(model_priority_list):
            self.logger.info(f"Attempting model [{model_index + 1}/{len(model_priority_list)}]: {model_id}")

            req = LLMChatRequest(messages=prompt, model=model_id)
            if temperature is not None:
                req.temperature = temperature

            if use_resilient_queue:
                try:
                    response = _execute_resilient_chat(
                        llm_manager,
                        req,
                        max_attempts=self.max_retries,
                        retry_delay=self.retry_delay,
                        cancellation_event=turn_cancellation,
                        deadline_seconds=remaining(),
                    )
                    if model_index > 0:
                        self.logger.info(
                            f"Successfully used fallback model: {model_id} "
                            f"(priority: {model_index + 1}/{len(model_priority_list)})"
                        )
                    else:
                        self.logger.debug(f"Successfully used primary model: {model_id}")
                    return {"resp": response}
                except FailoverExecutionError as error:
                    last_error = error
                    if not _can_fallback_to_next_model(error):
                        self.logger.error(
                            f"Model {model_id} failed with a non-retryable provider error; "
                            "stopping model fallback"
                        )
                        raise
                    self.logger.warning(
                        f"All eligible providers for model {model_id} failed; trying the next model"
                    )
                    continue

            # 对每个模型进行重试
            for retry_count in range(self.max_retries):
                try:
                    llm = llm_manager.get_llm(model_id)
                    if not llm:
                        self.logger.warning(f"LLM {model_id} not found, skipping to next model")
                        break  # 跳到下一个模型

                    # 尝试调用模型
                    response = llm.chat(req)

                    # 成功返回
                    if model_index > 0 or retry_count > 0:
                        self.logger.info(
                            f"Successfully used fallback model: {model_id} "
                            f"(priority: {model_index + 1}/{len(model_priority_list)}, "
                            f"retry: {retry_count + 1}/{self.max_retries})"
                        )
                    else:
                        self.logger.debug(f"Successfully used primary model: {model_id}")

                    return {"resp": response}

                except Exception as e:
                    last_error = e
                    error_msg = str(e)

                    # 判断是否应该重试
                    if retry_count < self.max_retries - 1:
                        self.logger.warning(
                            f"Model {model_id} failed (attempt {retry_count + 1}/{self.max_retries}): {error_msg}. Retrying in {self.retry_delay}s..."
                        )
                        time.sleep(self.retry_delay)
                    else:
                        self.logger.error(
                            f"Model {model_id} failed after {self.max_retries} attempts: {error_msg}"
                        )

                        # 如果还有备用模型，尝试下一个
                        if model_index < len(model_priority_list) - 1:
                            self.logger.info(f"Switching to next fallback model...")
                        break  # 跳到下一个模型

        # 所有模型都失败了
        error_details = f"All {len(model_priority_list)} model(s) failed after {self.max_retries} retries each. Models tried: {', '.join(model_priority_list)}"
        self.logger.error(error_details)

        if last_error:
            raise ValueError(f"{error_details}. Last error: {last_error}") from last_error
        else:
            raise ValueError(error_details)


class ChatResponseConverter(Block):
    name = "chat_response_converter"
    description = "把模型回复转成可发送的聊天消息，并按 <break> 拆分为多条，模拟真人分段发送。"
    inputs = {"resp": Input("resp", "LLM 响应", LLMChatResponse, "模型返回的回复")}
    outputs = {"msg": Output("msg", "IM 消息", IMMessage, "转换后的聊天消息")}
    container: DependencyContainer

    def execute(self, resp: LLMChatResponse) -> Dict[str, Any]:
        message_elements: List[MessageElement] = []
        
        for part in resp.message.content:
            if isinstance(part, LLMChatTextContent):
                # 通过 <break> 将回答分为不同的 TextMessage
                for element in part.text.split("<break>"):
                    if element.strip():
                        message_elements.append(TextMessage(element.strip()))
            elif isinstance(part, LLMChatImageContent):
                message_elements.append(ImageMessage(media_id=part.media_id))
        msg = IMMessage(sender=ChatSender.get_bot_sender(), message_elements=message_elements)
        return {"msg": msg}

class ExampleFunction(Block, ABC):
    """
    这个块是抽象function block，没有实际功能，你可以继承这个类，也可以参考这个类自己实现（遵从inputs, outputs格式约定）。
    """
    name = "tool"
    description = "抽象示例块，本身不可用，供开发者继承实现自定义的工具执行节点。"
    inputs = {
        "im_msg": Input("im_msg", "im 消息", IMMessage, "im 消息", True),
        "tool_call": Input("call_tools", "llm 回应", LLMChatResponse, "接收llm 的函数调用请求，你应该执行函数调用", True),
    }
    outputs = {
        "send_memory": Output("send_memory", "发送记忆模块", list[LLMChatMessage], "你应该在将函数调用期间的llm对话记录存储到记忆模块中"),
        # TODO: 请将所有LLMChatMessage 整合为一个LLMChatRequest。包含tool调用过程中的toolCallContent和toolResultContent。
        # EXAMPLE: 将接收到的call_tools: LLMChatResponse 中LLMChatMessage提取出来，设定role为assistance, 
        # 然后将所有函数调用结果按照LLMChaMessage(role="tool", content=[LLMToolResultContent])，整合为另外一个LLMChatMessage，拼接到上次构建的LLMChatRequest中。
        "request_body": Output("tool_result", "工具回应", LLMChatRequest, "请将全部上下文信息整合为LLMChatRequest")
    }
    container: DependencyContainer

    @abstractmethod
    def __init__(self):
        pass
    
    @abstractmethod
    def execute(self, **kwargs) -> dict[str, Any]:
        return super().execute(**kwargs)
    
class FunctionCalling(Block):
    """
    这个类只负责联系llm, 请将tools变量或者将tool_result变量整合为LLMChatRequest传入。注意同时传入tool_call和tool_result信息。
    注意: 你实现的function block 应该将too_result存入memory中, 本块不会自动存入函数调用期间的llm对话记录.

    具体block信息流转流程图将放置于后续教程中。详情请参见kirara wiki function calling部分。
    """
    name = "function_calling"
    description = "仅负责把函数调用请求发给模型并返回结果，工具的实际执行需由你自己的节点完成。多数场景更推荐使用「LLM: 执行对话并调用工具」。"
    inputs = {
        "request_body": Input("request_body", "llm 函数调用请求体", LLMChatRequest, "传递一个规范的函数调用请求体"),
    }
    outputs = {
        "resp": Output("resp", "llm 回应", LLMChatResponse, "返回的response, llm认为无需调用tool或者根据tool结果返回"),
        "tool_call": Output("call_tools", "llm 回应", LLMChatResponse, "返回的response带有tool_calls字段，你需要根据此字段进行下一个动作")
    }
    container: DependencyContainer

    def __init__(
        self,
        model_name: Annotated[
            str,
            # 等待实现： 只列出支持function_calling的模型
            ParamMeta(label="模型 ID1, 支持函数调用且不可为空", description="支持函数调用的主模型", options_provider=model_name_options_provider)
        ],
        fallback_model_1: Annotated[
            Optional[str],
            ParamMeta(label="模型 ID2", description="备用模型 ID2", options_provider=model_name_options_provider),
        ] = None,
        fallback_model_2: Annotated[
            Optional[str],
            ParamMeta(label="模型 ID3", description="备用模型 ID3", options_provider=model_name_options_provider),
        ] = None,
        fallback_model_3: Annotated[
            Optional[str],
            ParamMeta(label="模型 ID4", description="备用模型 ID4", options_provider=model_name_options_provider),
        ] = None,
        fallback_model_4: Annotated[
            Optional[str],
            ParamMeta(label="模型 ID5", description="备用模型 ID5", options_provider=model_name_options_provider),
        ] = None,
        max_retries: Annotated[
            int,
            ParamMeta(label="最大重试次数", description="每个模型的最大重试次数"),
        ] = 3,
        retry_delay: Annotated[
            float,
            ParamMeta(label="重试延迟(秒)", description="每次重试之间的等待时间"),
        ] = 3.0,
    ):
        self.model_name = model_name
        # 将4个备用模型参数组合成列表，过滤掉空值
        fallback_list = [fallback_model_1, fallback_model_2, fallback_model_3, fallback_model_4]
        self.fallback_models = [m for m in fallback_list if m] or None
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.logger = get_logger("FunctionCallingBlock")

    def execute(self, request_body: LLMChatRequest) -> Dict[str, Any]:
        if not self.model_name:
            # 预设 function_calling.yaml 的 model_name 故意留空，等用户从下拉框里选；
            # 报错必须说清是哪个节点，否则用户只知道"报错了"，不知道去哪里点。
            raise ValueError(
                f"节点「{self.name}」尚未选择模型：请在工作流编辑器中打开该节点，"
                f"从「模型 ID1」下拉框里选择一个支持函数调用（Function Calling）的模型"
                f"（need a model name which support function calling）"
            )

        llm_manager = self.container.resolve(LLMManager)

        # 构建模型优先级列表
        model_priority_list = [self.model_name]

        # 添加备用模型列表，去除与主模型及彼此之间的重复项
        if self.fallback_models:
            for fallback_model in self.fallback_models:
                if fallback_model and fallback_model not in model_priority_list:
                    model_priority_list.append(fallback_model)

        # 记录模型优先级列表
        self.logger.info(f"Function calling model priority list: {model_priority_list}")

        # 尝试每个模型
        last_error = None
        use_resilient_queue = _uses_resilient_provider_queue(llm_manager)
        # 与上面的对话节点同一套语义：整轮共享取消信号与递减预算。
        turn_cancellation, turn_budget = _turn_budget(self.container)
        turn_started = time.monotonic()

        def remaining() -> Optional[float]:
            if turn_budget is None:
                return None
            left = turn_budget - (time.monotonic() - turn_started)
            if left <= 0:
                if turn_cancellation is not None:
                    turn_cancellation.set()
                return 0.0
            return left

        for model_index, model_id in enumerate(model_priority_list):
            self.logger.info(f"Attempting function calling with model [{model_index + 1}/{len(model_priority_list)}]: {model_id}")

            request_body.model = model_id
            if use_resilient_queue:
                try:
                    response = _execute_resilient_chat(
                        llm_manager,
                        request_body,
                        max_attempts=self.max_retries,
                        retry_delay=self.retry_delay,
                        cancellation_event=turn_cancellation,
                        deadline_seconds=remaining(),
                    )
                    if model_index > 0:
                        self.logger.info(
                            f"Successfully used fallback model for function calling: {model_id} "
                            f"(priority: {model_index + 1}/{len(model_priority_list)})"
                        )
                    else:
                        self.logger.debug(f"Successfully used primary model for function calling: {model_id}")

                    if not response.message.tool_calls:
                        self.logger.debug("No tool calls found, return response directly")
                        return {"resp": response}
                    self.logger.debug("Tool calls found, return response with tool calls")
                    return {"tool_call": response}
                except FailoverExecutionError as error:
                    last_error = error
                    if not _can_fallback_to_next_model(error):
                        self.logger.error(
                            f"Function calling model {model_id} failed with a non-retryable provider error; "
                            "stopping model fallback"
                        )
                        raise
                    self.logger.warning(
                        f"All eligible providers for function calling model {model_id} failed; "
                        "trying the next model"
                    )
                    continue

            # 对每个模型进行重试
            for retry_count in range(self.max_retries):
                try:
                    llm = llm_manager.get_llm(model_id)
                    if not llm:
                        self.logger.warning(f"LLM {model_id} not found, skipping to next model")
                        break  # 跳到下一个模型

                    response: LLMChatResponse = llm.chat(request_body)

                    # 成功返回
                    if model_index > 0 or retry_count > 0:
                        self.logger.info(
                            f"Successfully used fallback model for function calling: {model_id} "
                            f"(priority: {model_index + 1}/{len(model_priority_list)}, "
                            f"retry: {retry_count + 1}/{self.max_retries})"
                        )
                    else:
                        self.logger.debug(f"Successfully used primary model for function calling: {model_id}")

                    if not response.message.tool_calls:
                        self.logger.debug("No tool calls found, return response directly")
                        return {"resp": response}
                    else:
                        self.logger.debug("Tool calls found, return response with tool calls")
                        return {"tool_call": response}

                except Exception as e:
                    last_error = e
                    error_msg = str(e)

                    # 判断是否应该重试
                    if retry_count < self.max_retries - 1:
                        self.logger.warning(
                            f"Function calling model {model_id} failed (attempt {retry_count + 1}/{self.max_retries}): {error_msg}. Retrying in {self.retry_delay}s..."
                        )
                        time.sleep(self.retry_delay)
                    else:
                        self.logger.error(
                            f"Function calling model {model_id} failed after {self.max_retries} attempts: {error_msg}"
                        )

                        # 如果还有备用模型，尝试下一个
                        if model_index < len(model_priority_list) - 1:
                            self.logger.info(f"Switching to next fallback model for function calling...")
                        break  # 跳到下一个模型

        # 所有模型都失败了
        error_details = f"All {len(model_priority_list)} function calling model(s) failed after {self.max_retries} retries each. Models tried: {', '.join(model_priority_list)}"
        self.logger.error(error_details)

        if last_error:
            raise ValueError(f"{error_details}. Last error: {last_error}") from last_error
        else:
            raise ValueError(error_details)


class ChatCompletionWithTools(Block):
    """
    支持工具调用的LLM对话块
    """
    name = "chat_completion_with_tools"
    description = "带工具调用的模型对话：内部自动完成「模型请求工具 → 执行 → 回传结果」的循环，通常配合「MCP: 提供工具」使用。"
    inputs = {
        "msg": Input("msg", "LLM 对话记录", List[LLMChatMessage], "LLM 的 prompt，即由 system、user、assistant和工具调用及结果的完整对话记录"),
        "tools": Input("tools", "工具列表", List[Tool], "可供模型调用的工具列表")
    }
    outputs = {
        "resp": Output("resp", "LLM 消息回应", LLMChatResponse, "模型返回给用户的消息"),
        "iteration_msgs": Output("iteration_msgs", "中间步骤消息", List[ComposableMessageType], "迭代过程中产生的所有消息，可以用记忆存储")
    }

    container: DependencyContainer

    def __init__(self, model_name: Annotated[
        str,
        ParamMeta(
            label="模型 ID, 需要支持函数调用",
            description="支持函数调用的模型",
            options_provider=model_name_options_provider)
    ],
        max_iterations: Annotated[
        int,
        ParamMeta(
            label="最大迭代次数",
            description="允许调用模型请求的最大次数，在进行最后一次请求时，模型将不允许调用工具")
    ] = 4,
        temperature: Annotated[
        Optional[float],
        ParamMeta(
            label="采样温度",
            description="取值 0.0~2.0，越大回答越随机、越小越稳定。留空则使用命中的调度规则 metadata.temperature，两者都没有时交由模型自身默认值决定")
    ] = None):
        self.model_name = model_name
        self.max_iterations = max_iterations
        # 采样温度：节点上没填时会回落到调度规则的 metadata.temperature。
        self.temperature = temperature
        self.logger = get_logger("Block.ChatCompletionWithTools")

    def execute(self, msg: List[LLMChatMessage], tools: List[Tool]) -> Dict[str, Any]:
        if not self.model_name:
            # 预设 mcp_tools.yaml 的 model_name 故意留空，等用户从下拉框里选；
            # 报错必须说清是哪个节点，否则用户只知道"报错了"，不知道去哪里点。
            raise ValueError(
                f"节点「{self.name}」尚未选择模型：请在工作流编辑器中打开该节点，"
                f"从「模型 ID, 需要支持函数调用」下拉框里选择一个支持函数调用（Function Calling）的模型"
                f"（need a model name which support function calling）")
        else:
            self.logger.info(
                f"Using  model: {self.model_name} to execute function calling")

        loop = self.container.resolve(asyncio.AbstractEventLoop)
        llm_manager = self.container.resolve(LLMManager)
        use_resilient_queue = _uses_resilient_provider_queue(llm_manager)
        llm = None if use_resilient_queue else llm_manager.get_llm(self.model_name)
        if not use_resilient_queue and not llm:
            raise ValueError(
                f"LLM {self.model_name} not found, please check the model name")

        iteration_msgs: List[LLMChatMessage] = []
        iter_count = 0
        # 解析本次实际生效的采样温度（节点配置 > 调度规则 metadata > 不携带）
        temperature = resolve_temperature(self.container, self.temperature, self.logger)
        # 多轮工具循环最容易把一轮对话拖到无限长：预算在整个 while 上共享，
        # 而不是每次迭代重新给满。
        loop_cancellation, loop_budget = _turn_budget(self.container)
        loop_started = time.monotonic()

        def loop_remaining() -> Optional[float]:
            if loop_budget is None:
                return None
            left = loop_budget - (time.monotonic() - loop_started)
            if left <= 0:
                if loop_cancellation is not None:
                    loop_cancellation.set()
                return 0.0
            return left

        while iter_count < self.max_iterations:
            # 在这里指定llm的model
            self.logger.debug(
                f"Iteration {iter_count+1} of {self.max_iterations}")
            request_body = LLMChatRequest(
                messages=msg + iteration_msgs, model=self.model_name)
            if temperature is not None:
                request_body.temperature = temperature
            if tools is not None and len(tools) > 0:
                request_body.tools = tools

            # 最后一次迭代不调用工具
            if iter_count == self.max_iterations - 1:
                request_body.tool_choice = "none"

            tools_mapping = {t.name: t for t in tools}

            if use_resilient_queue:
                response = _execute_resilient_chat(
                    llm_manager,
                    request_body,
                    cancellation_event=loop_cancellation,
                    deadline_seconds=loop_remaining(),
                )
            else:
                response = llm.chat(request_body)
            iter_count += 1
            if response.message.tool_calls:
                iteration_msgs.append(response.message)
                self.logger.debug("Tool calls found, attempt to invoke tools")
                for tool_call in response.message.tool_calls:
                    function = tool_call.function
                    if function is None or not function.name:
                        raise ValueError("LLM工具调用缺少工具名称")
                    actual_tool = tools_mapping.get(function.name)
                    if actual_tool:
                        self.logger.debug(
                            f"Invoking tool: {actual_tool.name}({function.arguments})")
                        resp_future = asyncio.run_coroutine_threadsafe(
                            actual_tool.invokeFunc(tool_call), loop
                        )
                        tool_result_msg = LLMChatMessage(
                            role="tool", content=[resp_future.result()])
                        iteration_msgs.append(tool_result_msg)
            else:
                self.logger.debug(
                    "No tool calls found, return response directly")
                return {"resp": response, "iteration_msgs": iteration_msgs}

        return {"resp": response, "iteration_msgs": iteration_msgs}
