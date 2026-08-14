import random
from typing import Dict, List, Optional, Union

from typing_extensions import deprecated

from kirara_ai.config.global_config import GlobalConfig, ModelConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.events.llm import LLMAdapterLoaded, LLMAdapterUnloaded
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.llm.adapter import LLMBackendAdapter
from kirara_ai.llm.llm_registry import LLMBackendRegistry
from kirara_ai.llm.model_types import ModelAbility, ModelType
from kirara_ai.logger import get_logger


class LLMManager:
    """
    跟踪、管理和调度模型后端
    """

    container: DependencyContainer
    config: GlobalConfig
    backend_registry: LLMBackendRegistry
    active_backends: Dict[str, List[LLMBackendAdapter]]
    model_info: Dict[str, ModelConfig]  # 存储模型的配置信息
    event_bus: EventBus

    @Inject()
    def __init__(
        self,
        container: DependencyContainer,
        config: GlobalConfig,
        backend_registry: LLMBackendRegistry,
        event_bus: EventBus,
    ):
        self.container = container
        self.config = config
        self.backend_registry = backend_registry
        self.event_bus = event_bus
        self.logger = get_logger("LLMAdapter")
        self.active_backends = {}
        self.model_info = {}  # 初始化模型信息字典
        self.backends: Dict[str, LLMBackendAdapter] = {}

    def load_config(self):
        """加载配置文件中的所有启用的后端"""
        for backend in self.config.llms.api_backends:
            if backend.enable:
                self.logger.info(f"Loading backend: {backend.name}")
                try:
                    self.load_backend(backend.name)
                except Exception as e:
                    self.logger.error(f"Failed to load backend {backend.name}: {e}")

    def load_backend(self, backend_name: str):
        """
        加载指定的后端
        :param backend_name: 后端名称
        """
        backend = next(
            (b for b in self.config.llms.api_backends if b.name == backend_name), None
        )
        if not backend:
            raise ValueError(f"Backend {backend_name} not found in config")

        if not backend.enable:
            raise ValueError(f"Backend {backend_name} is not enabled")

        if any(backend_name in adapters for adapters in self.active_backends.values()):
            raise ValueError(f"Backend {backend_name} is already loaded")

        adapter_class = self.backend_registry.get(backend.adapter)
        config_class = self.backend_registry.get_config_class(backend.adapter)

        if not adapter_class or not config_class:
            raise ValueError(f"Invalid adapter type: {backend.adapter}")

        # 创建适配器实例
        with self.container.scoped() as scoped_container:
            scoped_container.register(config_class, config_class(**backend.config))
            adapter = Inject(scoped_container).create(adapter_class)()
            adapter.backend_name = backend_name
            self.backends[backend_name] = adapter

            # 注册到每个支持的模型并记录模型信息
            for model_config in backend.models:
                # 从ModelConfig中获取模型信息
                model_id = model_config.id

                # 直接存储模型配置
                self.model_info[model_id] = model_config

                if model_id not in self.active_backends:
                    self.active_backends[model_id] = []
                self.active_backends[model_id].append(adapter)

        self.event_bus.post(LLMAdapterLoaded(adapter=adapter, backend_name=backend_name))
        self.logger.info(f"Backend {backend_name} loaded successfully")

    async def unload_backend(self, backend_name: str):
        """
        卸载指定的后端
        :param backend_name: 后端名称
        """
        backend = next(
            (b for b in self.config.llms.api_backends if b.name == backend_name), None
        )
        if not backend:
            raise ValueError(f"Backend {backend_name} not found in config")

        backend_adapter = self.backends.get(backend_name)

        if not backend_adapter:
            raise ValueError(f"Backend {backend_name} not found")

        # 从所有模型中移除这个后端的适配器
        all_models = list(self.active_backends.keys())
        for model in all_models:
            if backend_adapter in self.active_backends[model]:
                self.active_backends[model].remove(backend_adapter)
            if len(self.active_backends[model]) == 0:
                self.active_backends.pop(model)
                # 清理模型信息
                if model in self.model_info:
                    self.model_info.pop(model)

        backend_adapter = self.backends.pop(backend_name)
        self.event_bus.post(LLMAdapterUnloaded(backend_name=backend_name, adapter=backend_adapter))

    async def reload_backend(self, backend_name: str):
        """
        重新加载指定的后端
        :param backend_name: 后端名称
        """
        await self.unload_backend(backend_name)
        self.load_backend(backend_name)

    def is_backend_available(self, backend_name: str) -> bool:
        """
        检查后端是否可用
        :param backend_name: 后端名称
        :return: 后端是否可用
        """
        backend = next(
            (b for b in self.config.llms.api_backends if b.name == backend_name), None
        )
        if not backend:
            return False

        if not backend.enable:
            return False

        # 检查后端的所有模型是否都有可用的适配器
        for model_config in backend.models:
            model_id = model_config.id
            if model_id not in self.active_backends or len(self.active_backends[model_id]) == 0:
                return False
        return True

    def get(self, backend_name: str) -> Optional[LLMBackendAdapter]:
        """
        获取指定后端的适配器实例
        :param backend_name: 后端名称
        :return: LLM适配器实例,如果没有找到则返回None
        """
        return self.backends.get(backend_name)

    def get_llm(self, model_id: str) -> Optional[LLMBackendAdapter]:
        """
        从指定模型的活跃后端中随机返回一个适配器实例
        :param model_id: 模型ID
        :return: LLM适配器实例,如果没有找到则返回None
        """
        if model_id not in self.active_backends:
            return None

        backends = self.active_backends[model_id]
        if not backends:
            return None
        # TODO: 后续考虑支持更多的选择策略
        return random.choice(backends)

    def get_supported_models(self, model_type: Union[ModelType, ModelAbility], ability: Optional[ModelAbility] = None) -> List[str]:
        """
        获取所有支持指定能力的模型
        :param model_type: 模型类型；为兼容 3.2.0 的单参数调用 get_supported_models(ability)，
                           当此处传入能力枚举且 ability 省略时，按 ModelType.LLM 处理
        :param ability: 指定的能力
        :return: 支持的模型ID列表
        """
        if ability is None:
            # 旧签名 get_supported_models(ability)
            ability = model_type  # type: ignore[assignment]
            model_type = ModelType.LLM
        # 注意：这里必须用“模型能力位包含所需能力位”的方向判断。
        # ModelAbility.is_capable 的实现是 self.value & ability == ability，
        # 若写成 ability.is_capable(model.ability)，含额外能力的模型
        #（例如 gpt-4o = TextChat|ImageInput|FunctionCalling = 286）会被误判为不支持 TextChat(14)。
        required = ability.value  # type: ignore[union-attr]
        return [
            model_id
            for model_id, model_config in self.model_info.items()
            if model_config.type == model_type.value  # type: ignore[union-attr]
            and (model_config.ability & required) == required
        ]

    @deprecated("请使用 get_supported_models 方法")
    def get_llm_id_by_ability(self, ability: ModelAbility) -> Optional[str]:
        """
        根据指定的能力获取一个随机符合要求的LLM模型ID
        deprecated: 请使用 get_supported_models 方法
        :param ability: 指定的能力
        :return: 符合要求的模型ID，如果没有找到则返回None
        """
        supported_models = self.get_supported_models(ModelType.LLM, ability)
        return None if not supported_models else random.choice(supported_models)

    def get_models_by_ability(self, model_type: ModelType, ability: ModelAbility) -> Optional[str]:
        """
        根据指定能力随机获取一个模型ID
        :param model_type: 模型类型
        :param ability: 指定的能力
        :return: 随机选择的模型ID，如果没有找到则返回None
        """
        supported_models = self.get_supported_models(model_type, ability)
        if not supported_models:
            return None
        return random.choice(supported_models)

    def get_models_by_type(self, model_type: ModelType) -> List[str]:
        """
        获取指定类型的所有模型
        :param model_type: 模型类型
        :return: 该类型的模型ID列表
        """
        return [
            model_id for model_id, config in self.model_info.items()
            if config.type == model_type.value
        ]
