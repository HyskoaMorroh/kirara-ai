from quart import Blueprint, g, jsonify, request

from kirara_ai.config.config_loader import CONFIG_FILE, ConfigLoader
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.llm.adapter import AutoDetectModelsProtocol
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.llm_registry import LLMBackendRegistry
from kirara_ai.llm.model_types import LLMAbility, ModelType
from kirara_ai.logger import get_logger
from kirara_ai.scheduler import TaskScheduler
from kirara_ai.web.api.llm.models import (LLMAdapterConfigSchema, LLMAdapterTypes, LLMBackendCreateRequest,
                                          LLMBackendInfo, LLMBackendList, LLMBackendListResponse, LLMBackendResponse,
                                          LLMBackendUpdateRequest, ModelConfigListResponse)

from ...auth.middleware import require_auth

llm_bp = Blueprint("llm", __name__)
logger = get_logger("WebServer.LLM")


@llm_bp.route("/types", methods=["GET"])
@require_auth
async def get_adapter_types():
    """获取所有可用的适配器类型"""
    registry: LLMBackendRegistry = g.container.resolve(LLMBackendRegistry)
    return LLMAdapterTypes(types=registry.get_adapter_types()).model_dump()


@llm_bp.route("/backends", methods=["GET"])
@require_auth
async def list_backends():
    """获取所有后端列表"""
    try:
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        backends = []
        for backend in config.llms.api_backends:
            backends.append(
                LLMBackendInfo(
                    name=backend.name,
                    adapter=backend.adapter,
                    config=backend.config,
                    enable=backend.enable,
                    models=backend.models,
                )
            )
        return LLMBackendListResponse(
            data=LLMBackendList(backends=backends)
        ).model_dump()
    except Exception as e:
        logger.opt(exception=e).error("Failed to list backends")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/backends/<backend_name>", methods=["GET"])
@require_auth
async def get_backend(backend_name: str):
    """获取指定后端信息"""
    try:
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        backend = next(
            (b for b in config.llms.api_backends if b.name == backend_name), None
        )
        if not backend:
            return jsonify({"error": f"Backend {backend_name} not found"}), 404

        return LLMBackendResponse(
            data=LLMBackendInfo(
                name=backend.name,
                adapter=backend.adapter,
                config=backend.config,
                enable=backend.enable,
                models=backend.models,
            )
        ).model_dump()
    except Exception as e:
        logger.opt(exception=e).error("Failed to get backend")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/backends", methods=["POST"])
@require_auth
async def create_backend():
    """创建新的后端"""
    try:
        data = await request.get_json()
        request_data = LLMBackendCreateRequest(**data)

        config: GlobalConfig = g.container.resolve(GlobalConfig)
        manager: LLMManager = g.container.resolve(LLMManager)

        # 检查后端名称是否已存在
        if any(b.name == request_data.name for b in config.llms.api_backends):
            return (
                jsonify({"error": f"Backend {request_data.name} already exists"}),
                400,
            )

        # 创建新的后端配置
        backend = LLMBackendInfo(
            name=request_data.name,
            adapter=request_data.adapter,
            config=request_data.config,
            enable=request_data.enable,
            models=request_data.models,
        )

        # 添加到配置中
        config.llms.api_backends.append(backend)

        # 如果启用则加载后端
        if backend.enable:
            manager.load_backend(backend.name)

        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
        return LLMBackendResponse(data=backend).model_dump()
    except Exception as e:
        logger.opt(exception=e).error("Failed to create backend")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/backends/<backend_name>", methods=["PUT"])
@require_auth
async def update_backend(backend_name: str):
    """更新指定后端"""
    try:
        data = await request.get_json()
        request_data = LLMBackendUpdateRequest(**data)

        config: GlobalConfig = g.container.resolve(GlobalConfig)
        manager: LLMManager = g.container.resolve(LLMManager)

        # 查找要更新的后端
        backend_index = next(
            (
                i
                for i, b in enumerate(config.llms.api_backends)
                if b.name == backend_name
            ),
            -1,
        )
        if backend_index == -1:
            return jsonify({"error": f"Backend {backend_name} not found"}), 404

        # 创建更新后的后端配置
        updated_backend = LLMBackendInfo(
            name=request_data.name,
            adapter=request_data.adapter,
            config=request_data.config,
            enable=request_data.enable,
            models=request_data.models,
        )

        # 如果原后端已启用，先卸载
        if config.llms.api_backends[backend_index].enable:
            await manager.unload_backend(backend_name)

        # 更新配置
        config.llms.api_backends[backend_index] = updated_backend

        # 如果新配置启用则加载后端
        if updated_backend.enable:
            manager.load_backend(updated_backend.name)
        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
        return LLMBackendResponse(data=updated_backend).model_dump()
    except Exception as e:
        logger.opt(exception=e).error("Failed to update backend")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/backends/<backend_name>", methods=["DELETE"])
@require_auth
async def delete_backend(backend_name: str):
    """删除指定后端"""
    try:
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        manager: LLMManager = g.container.resolve(LLMManager)

        # 查找要删除的后端
        backend_index = next(
            (
                i
                for i, b in enumerate(config.llms.api_backends)
                if b.name == backend_name
            ),
            -1,
        )
        if backend_index == -1:
            return jsonify({"error": f"Backend {backend_name} not found"}), 404

        backend = config.llms.api_backends[backend_index]
        # 如果后端已启用，要卸载
        if backend.enable:
            await manager.unload_backend(backend_name)
            
        # 从配置中删除
        deleted_backend = config.llms.api_backends.pop(backend_index)

        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
            
        return LLMBackendResponse(
            data=LLMBackendInfo(
                name=deleted_backend.name,
                adapter=deleted_backend.adapter,
                config=deleted_backend.config,
                enable=deleted_backend.enable,
                models=deleted_backend.models,
            )
        ).model_dump()
    except Exception as e:
        logger.opt(exception=e).error("Failed to delete backend")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/types/<adapter_type>/config-schema", methods=["GET"])
@require_auth
async def get_adapter_config_schema(adapter_type: str):
    """获取指定适配器类型的配置字段模式"""
    try:
        registry: LLMBackendRegistry = g.container.resolve(LLMBackendRegistry)
        config_class = registry.get_config_class(adapter_type)
        if not config_class:
            return jsonify({"error": f"Adapter type {adapter_type} not found"}), 404

        schema = config_class.model_json_schema()
        return LLMAdapterConfigSchema(configSchema=schema).model_dump()
    except Exception as e:
        logger.opt(exception=e).error("Failed to get adapter config schema")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/types/<adapter_type>/supports-auto-detect-models", methods=["GET"])
@require_auth
async def supports_auto_detect_models(adapter_type: str):
    """检查指定适配器类型是否支持自动检测模型"""
    try:
        registry: LLMBackendRegistry = g.container.resolve(LLMBackendRegistry)
        adapter_class = registry.get(adapter_type)
        if not adapter_class:
            return jsonify({"error": f"Adapter type {adapter_type} not found"}), 404
        if not issubclass(adapter_class, AutoDetectModelsProtocol):
            return (
                jsonify(
                    {
                        "error": f"Adapter type {adapter_type} does not support auto-detect models"
                    }
                ),
                400,
            )
        return jsonify({"supportsAutoDetectModels": True})
    except Exception as e:
        logger.opt(exception=e).error("Failed to check if adapter supports auto-detect models")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/backends/<backend_name>/auto-detect-models", methods=["GET"])
@require_auth
async def auto_detect_models(backend_name: str):
    """自动检测指定后端的模型列表"""
    try:
        manager: LLMManager = g.container.resolve(LLMManager)
        adapter = manager.get(backend_name)
        if not adapter:
            return jsonify({"error": f"Backend {backend_name} not found"}), 404
        if not isinstance(adapter, AutoDetectModelsProtocol):
            return (
                jsonify(
                    {
                        "error": f"Backend {backend_name} does not support auto-detect models"
                    }
                ),
                400,
            )
        # 自动检测模型并返回完整的 ModelConfig 列表
        models = await adapter.auto_detect_models()

        # 确保每个模型都有正确的能力设置
        for model in models:
            # 如果模型类型是LLM但没有设置能力，设置默认的TextChat能力
            if model.type == ModelType.LLM.value and not model.ability:
                model.ability = LLMAbility.TextChat.value

        return ModelConfigListResponse(models=models).model_dump()
    except Exception as e:
        logger.opt(exception=e).error("Failed to auto-detect models")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/auto-detect-schedule", methods=["GET"])
@require_auth
async def get_auto_detect_schedule():
    """获取所有后端的自动检测计划与上次执行时间"""
    try:
        scheduler: TaskScheduler = g.container.resolve(TaskScheduler)
        return jsonify(scheduler.get_status())
    except Exception as e:
        logger.opt(exception=e).error("Failed to get auto-detect schedule")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/backends/<backend_name>/auto-detect-schedule", methods=["PUT"])
@require_auth
async def update_auto_detect_schedule(backend_name: str):
    """设置指定后端的自动检测间隔天数，0 表示关闭"""
    try:
        data = await request.get_json()
        interval_days = int(data.get("interval_days", 0))
        if interval_days < 0:
            return jsonify({"error": "interval_days must be >= 0"}), 400

        config: GlobalConfig = g.container.resolve(GlobalConfig)
        backend = next(
            (b for b in config.llms.api_backends if b.name == backend_name), None
        )
        if not backend:
            return jsonify({"error": f"Backend {backend_name} not found"}), 404

        backend.auto_detect_interval_days = interval_days
        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
        logger.info(
            f"Backend {backend_name} auto-detect interval set to {interval_days} day(s)"
        )
        return jsonify({"name": backend_name, "interval_days": interval_days})
    except Exception as e:
        logger.opt(exception=e).error("Failed to update auto-detect schedule")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/auto-detect-schedule/run", methods=["POST"])
@require_auth
async def run_auto_detect_now():
    """立即对所有配置了自动检测的后端执行一次检测"""
    try:
        scheduler: TaskScheduler = g.container.resolve(TaskScheduler)
        results = await scheduler.run_once(force=True)
        return jsonify({"results": results})
    except Exception as e:
        logger.opt(exception=e).error("Failed to run auto-detect")
        return jsonify({"error": str(e)}), 500
