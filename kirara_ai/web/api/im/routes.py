import asyncio
from copy import deepcopy
from typing import Any
from uuid import uuid4

from quart import Blueprint, g, jsonify, request

from kirara_ai.config.config_loader import CONFIG_FILE, ConfigJsonSchema, ConfigLoader
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.im.adapter import (
    AdapterHealthProvider,
    AdapterHealthSnapshot,
    BotProfileAdapter,
)
from kirara_ai.im.im_registry import IMRegistry
from kirara_ai.im.manager import IMManager
from kirara_ai.logger import get_logger

from ...auth.middleware import require_auth
from .models import (IMAdapterConfig, IMAdapterConfigSchema, IMAdapterList, IMAdapterResponse, IMAdapterStatus,
                     IMAdapterTypes)

im_bp = Blueprint("im", __name__)

logger = get_logger("Web.IM")


SENSITIVE_CONFIG_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
}


def _is_sensitive_config_key(key: object) -> bool:
    return str(key).strip().lower() in SENSITIVE_CONFIG_KEYS


def _redact_sensitive_config(value: Any) -> Any:
    """Return an API-safe copy without adapter credentials."""
    if isinstance(value, dict):
        return {
            key: "" if _is_sensitive_config_key(key) else _redact_sensitive_config(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_config(item) for item in value]
    return deepcopy(value)


def _restore_unchanged_secrets(submitted: Any, current: Any) -> Any:
    """Treat blank secret fields in update requests as keep-existing placeholders."""
    if not isinstance(submitted, dict) or not isinstance(current, dict):
        return deepcopy(submitted)

    restored: dict[str, Any] = {}
    for key, value in submitted.items():
        old_value = current.get(key)
        if _is_sensitive_config_key(key) and value == "" and key in current:
            restored[key] = deepcopy(old_value)
        else:
            restored[key] = _restore_unchanged_secrets(value, old_value)
    return restored


def _create_adapter(manager: IMManager, name: str, adapter: str, config: dict):
    registry: IMRegistry = g.container.resolve(IMRegistry)
    adapter_info = registry.get_all_adapters()[adapter]
    adapter_class = adapter_info.adapter_class
    adapter_config_class = adapter_info.config_class
    adapter_config = adapter_config_class(**config)
    return manager.create_adapter(name, adapter_class, adapter_config)


def _get_adapter_health(
    manager: IMManager, adapter_id: str
) -> AdapterHealthSnapshot | None:
    try:
        adapter = manager.get_adapter(adapter_id)
        if not isinstance(adapter, AdapterHealthProvider):
            return None
        return adapter.get_health_snapshot()
    except Exception as exc:
        logger.warning(f"Failed to get adapter health for {adapter_id}: {exc}")
        return None


def _can_query_bot_profile(
    is_running: bool, health: AdapterHealthSnapshot | None
) -> bool:
    return is_running and (health is None or health.status == "connected")


async def _cleanup_adapter(
    manager: IMManager,
    adapter_id: str,
    adapter: Any,
    loop: asyncio.AbstractEventLoop,
    start_attempted: bool,
) -> None:
    """Stop a candidate that may have acquired resources before removing it."""
    if start_attempted:
        try:
            if manager.adapters.get(adapter_id) is adapter and getattr(
                adapter, "is_running", False
            ):
                await manager.stop_adapter(adapter_id, loop)
            else:
                # A failed adapter.start() may have allocated resources before
                # raising and therefore cannot be identified by is_running.
                await adapter.stop()
                adapter.is_running = False
        except Exception as exc:
            logger.warning(f"Failed to clean up adapter candidate '{adapter_id}': {exc}")
            try:
                await adapter.stop()
            except Exception as retry_exc:
                logger.warning(
                    f"Failed to retry cleanup for adapter candidate '{adapter_id}': {retry_exc}"
                )
            adapter.is_running = False
    if manager.adapters.get(adapter_id) is adapter:
        manager.adapters.pop(adapter_id, None)


@im_bp.route("/types", methods=["GET"])
@require_auth
async def get_adapter_types():
    """获取所有可用的适配器类型"""
    registry: IMRegistry = g.container.resolve(IMRegistry)
    adapters = registry.get_all_adapters()
    types = [info.name for info in adapters.values()]
    return IMAdapterTypes(types=types, adapters=adapters).model_dump()


@im_bp.route("/adapters", methods=["GET"])
@require_auth
async def list_adapters():
    """获取所有已配置的适配器"""
    config = g.container.resolve(GlobalConfig)
    manager = g.container.resolve(IMManager)
    adapters = []
    for im in config.ims:
        is_running = manager.is_adapter_running(im.name)
        configs = _redact_sensitive_config(im.config)
        health = _get_adapter_health(manager, im.name)
        adapters.append(
            IMAdapterStatus(
                name=im.name,
                adapter=im.adapter,
                is_running=is_running,
                config=configs,
                health=health,
            )
        )

    return IMAdapterList(adapters=adapters).model_dump(exclude_none=True)


@im_bp.route("/adapters/<adapter_id>", methods=["GET"])
@require_auth
async def get_adapter(adapter_id: str):
    """获取特定适配器的信息"""
    manager: IMManager = g.container.resolve(IMManager)

    # 查找适配器类型
    if not manager.has_adapter(adapter_id):
        return jsonify({"error": "Adapter not found"}), 404

    adapter_config = manager.get_adapter_config(adapter_id)
    adapter = manager.get_adapter(adapter_id)
    is_running = manager.is_adapter_running(adapter_id)
    health = _get_adapter_health(manager, adapter_id)
    bot_profile = None
    if _can_query_bot_profile(is_running, health) and isinstance(
        adapter, BotProfileAdapter
    ):
        try:
            bot_profile = await asyncio.wait_for(adapter.get_bot_profile(), timeout=5.0)
        except Exception as exc:
            logger.warning(f"Failed to get bot profile for {adapter_id}: {exc}")
        
    return IMAdapterResponse(
        adapter=IMAdapterStatus(
            name=adapter_id,
            adapter=adapter_config.adapter,
            is_running=is_running,
            config=_redact_sensitive_config(adapter_config.config),
            bot_profile=bot_profile,
            health=health,
        )
    ).model_dump(exclude_none=True)


@im_bp.route("/adapters", methods=["POST"])
@require_auth
async def create_adapter():
    """创建新的适配器"""
    data = await request.get_json()
    adapter_info = IMAdapterConfig(**data)

    config: GlobalConfig = g.container.resolve(GlobalConfig)
    registry: IMRegistry = g.container.resolve(IMRegistry)
    manager: IMManager = g.container.resolve(IMManager)

    # 检查适配器类型是否存在
    if adapter_info.adapter not in registry.get_all_adapters():
        return jsonify({"error": "Invalid adapter type"}), 400

    # 检查ID是否已存在
    if manager.has_adapter(adapter_info.name):
        return jsonify({"error": "Adapter ID already exists"}), 400

    previous_configs = deepcopy(config.ims)
    created_adapter = None
    start_attempted = False
    try:
        # 先创建并启动实例，配置文件仍保持原状，便于失败时完整回滚。
        created_adapter = _create_adapter(
            manager, adapter_info.name, adapter_info.adapter, adapter_info.config
        )
        if adapter_info.enable:
            start_attempted = True
            await manager.start_adapter(adapter_info.name, asyncio.get_event_loop())
        config.ims.append(adapter_info)
        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
    except Exception as exc:
        if created_adapter is not None:
            await _cleanup_adapter(
                manager,
                adapter_info.name,
                created_adapter,
                asyncio.get_event_loop(),
                start_attempted,
            )
        config.ims = previous_configs
        logger.error(f"Failed to create adapter '{adapter_info.name}': {exc}")
        return jsonify({"error": str(exc)}), 500

    is_running = manager.is_adapter_running(adapter_info.name)
    health = _get_adapter_health(manager, adapter_info.name)

    return IMAdapterResponse(
        adapter=IMAdapterStatus(
            name=adapter_info.name,
            adapter=adapter_info.adapter,
            is_running=is_running,
            config=_redact_sensitive_config(adapter_info.config),
            health=health,
        )
    ).model_dump(exclude_none=True)


@im_bp.route("/adapters/<adapter_id>", methods=["PUT"])
@require_auth
async def update_adapter(adapter_id: str):
    """更新适配器配置 (支持重命名)"""
    data = await request.get_json()
    try:
        adapter_info = IMAdapterConfig(**data)
    except Exception as e:
        return jsonify({"error": f"Invalid request data: {e}"}), 400

    config: GlobalConfig = g.container.resolve(GlobalConfig)
    manager: IMManager = g.container.resolve(IMManager)
    registry: IMRegistry = g.container.resolve(IMRegistry)
    loop = asyncio.get_event_loop()

    # 1. 检查原始适配器是否存在
    if not manager.has_adapter(adapter_id):
        return jsonify({"error": "Adapter not found"}), 404

    # 2. 如果名称改变，检查新名称是否冲突
    if adapter_id != adapter_info.name and manager.has_adapter(adapter_info.name):
        return jsonify({"error": f"Adapter name '{adapter_info.name}' already exists"}), 400

    # 3. 检查适配器类型是否有效
    if adapter_info.adapter not in registry.get_all_adapters():
        return jsonify({"error": "Invalid adapter type specified"}), 400

    current_config = manager.get_adapter_config(adapter_id)
    if current_config.adapter == adapter_info.adapter:
        adapter_info.config = _restore_unchanged_secrets(
            adapter_info.config, current_config.config
        )

    old_configs = deepcopy(config.ims)
    old_adapter = manager.get_adapter(adapter_id)
    old_was_running = manager.is_adapter_running(adapter_id)
    old_config_index = next(
        (index for index, item in enumerate(old_configs) if item.name == adapter_id),
        None,
    )
    if old_config_index is None:
        return jsonify({"error": "Adapter configuration not found"}), 404

    temporary_name = f"__im_update_{uuid4().hex}"
    replacement = None
    replacement_start_attempted = False

    async def rollback_update() -> str | None:
        """Remove the candidate and restore the previous instance/configuration."""
        rollback_errors: list[str] = []
        if replacement is not None:
            await _cleanup_adapter(
                manager,
                temporary_name,
                replacement,
                loop,
                replacement_start_attempted,
            )
        manager.adapters.pop(adapter_info.name, None)
        manager.adapters[adapter_id] = old_adapter
        config.ims = deepcopy(old_configs)
        try:
            if old_was_running and not manager.is_adapter_running(adapter_id):
                await manager.start_adapter(adapter_id, loop)
        except Exception as rollback_exc:
            rollback_errors.append(f"failed to restart previous adapter: {rollback_exc}")
        return "; ".join(rollback_errors) if rollback_errors else None

    try:
        # 验证新配置并创建候选实例。此时旧实例和旧配置仍可回滚。
        replacement = _create_adapter(
            manager, temporary_name, adapter_info.adapter, adapter_info.config
        )

        if old_was_running:
            await manager.stop_adapter(adapter_id, loop)

        if adapter_info.enable:
            replacement_start_attempted = True
            await manager.start_adapter(temporary_name, loop)

        new_configs = deepcopy(old_configs)
        new_configs[old_config_index] = adapter_info
        config.ims = new_configs
        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
    except Exception as exc:
        rollback_error = await rollback_update()
        logger.error(f"Failed to update adapter '{adapter_id}': {exc}")
        error = str(exc)
        if rollback_error:
            error = f"{error}; rollback failed: {rollback_error}"
        return jsonify({"error": error}), 500

    # 提交：新配置已经落盘，才替换管理器中的公开名称。
    manager.adapters.pop(adapter_id, None)
    manager.adapters.pop(temporary_name, None)
    manager.adapters[adapter_info.name] = replacement
    is_now_running = manager.is_adapter_running(adapter_info.name)

    # --- 准备并返回响应 ---
    bot_profile = None
    adapter_instance = manager.get_adapter(adapter_info.name)
    health = _get_adapter_health(manager, adapter_info.name)
    if _can_query_bot_profile(is_now_running, health):
        if isinstance(adapter_instance, BotProfileAdapter):
            try:
                # 添加超时以防卡住
                bot_profile = await asyncio.wait_for(adapter_instance.get_bot_profile(), timeout=5.0)
            except Exception as e:
                logger.error(f"Failed to get bot profile for {adapter_info.name} after update: {e}")

    return IMAdapterResponse(adapter=IMAdapterStatus(
        name=adapter_info.name, # 使用新名称
        adapter=adapter_info.adapter,
        is_running=is_now_running, # 反映当前实际运行状态
        config=_redact_sensitive_config(adapter_info.config),
        bot_profile=bot_profile,
        health=health
    )).model_dump(exclude_none=True)


@im_bp.route("/adapters/<adapter_id>", methods=["DELETE"])
@require_auth
async def delete_adapter(adapter_id: str):
    """删除适配器"""
    config: GlobalConfig = g.container.resolve(GlobalConfig)
    manager: IMManager = g.container.resolve(IMManager)
    loop = asyncio.get_event_loop()

    # 先停止适配器
    if manager.is_adapter_running(adapter_id):
        await manager.stop_adapter(adapter_id, loop)

    # 从配置中删除
    manager.delete_adapter(adapter_id)

    # 保存配置到文件
    ConfigLoader.save_config_with_backup(CONFIG_FILE, config)

    return jsonify({"message": "Adapter deleted successfully"})


@im_bp.route("/adapters/<adapter_id>/start", methods=["POST"])
@require_auth
async def start_adapter(adapter_id: str):
    """启动适配器"""
    manager: IMManager = g.container.resolve(IMManager)
    loop = asyncio.get_event_loop()

    if manager.is_adapter_running(adapter_id):
        return jsonify({"error": "Adapter is already running"}), 400

    try:
        await manager.start_adapter(adapter_id, loop)
        return jsonify({"message": "Adapter started successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@im_bp.route("/adapters/<adapter_id>/stop", methods=["POST"])
@require_auth
async def stop_adapter(adapter_id: str):
    """停止适配器"""
    manager: IMManager = g.container.resolve(IMManager)
    loop = asyncio.get_event_loop()

    if not manager.is_adapter_running(adapter_id):
        return jsonify({"error": "Adapter is not running"}), 400

    try:
        await manager.stop_adapter(adapter_id, loop)
        return jsonify({"message": "Adapter stopped successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@im_bp.route("/adapters/<adapter_id>/qr-login", methods=["POST"])
@require_auth
async def refresh_qr_login(adapter_id: str):
    """Re-read the upstream implementation's log and return the newest QR state.

    需求 18.4 点名「刷新动作」。此前扫码状态只随整份适配器信息返回，
    而二维码的有效期实测 120 秒——远短于操作者「看一眼、去拿手机、回来扫」
    这个动作序列。他因此总在扫一张屏幕上还在、上游其实已经换掉的码，
    这正是「二维码总是过期，无法登录」的形态。

    **刷新的语义只到重读为止。** Kirara 不生成二维码，LLOneBot / PMHQ 在自己
    的容器里生成并在过期时自行重新请求。这个动作做的是「立刻重读那份日志，
    给出最新一张码的状态」，不是「让上游重新生成一张」——把后者写进按钮文案
    是对所有权的谎报：点了没反应时操作者会去排查 Kirara，而要看的是上游容器。

    三种「拿不到」严格分开，因为处置完全不同：

    - 适配器不存在 → 404；
    - 适配器没有扫码这回事（Telegram / WeCom）→ ``supported: false``；
    - 支持但没配日志路径 → ``supported: true`` 且给出要填哪个配置项。
      把「没开这个功能」显示成「读不到事件」会让人去查挂载，
      而要做的只是填一个配置项。

    响应里没有二维码内容，只有路径：二维码是登录凭据材料，
    状态面板不该成为它流经的地方；扫码在上游自己的 WebUI 完成。
    """
    manager: IMManager = g.container.resolve(IMManager)
    adapter = manager.adapters.get(adapter_id)
    if adapter is None:
        return jsonify({"error": "Adapter not found"}), 404

    reader = getattr(adapter, "_read_qr_login_snapshot", None)
    if not callable(reader):
        return jsonify(
            {
                "supported": False,
                "qr_login": None,
                "remediation": "该适配器没有扫码登录环节，无需刷新。",
            }
        )

    try:
        snapshot = reader()
    except Exception as exc:  # noqa: BLE001 - 观测不能成为新的失败点
        logger.warning(f"Failed to refresh QR login state for {adapter_id}: {exc}")
        return jsonify(
            {
                "supported": True,
                "qr_login": None,
                "remediation": "读取 OneBot 实现日志失败；确认该路径在容器内可读。",
            }
        )

    if snapshot is None:
        return jsonify(
            {
                "supported": True,
                "qr_login": None,
                "remediation": (
                    "未配置 qr_login_log_path；填入 OneBot 实现（LLOneBot / PMHQ）"
                    "的日志文件路径后即可看到扫码生命周期。"
                ),
            }
        )

    return jsonify(
        {
            "supported": True,
            "qr_login": snapshot.model_dump(mode="json"),
            "remediation": snapshot.remediation,
        }
    )


@im_bp.route("/types/<adapter_type>/config-schema", methods=["GET"])
@require_auth
async def get_adapter_config_schema(adapter_type: str):
    """获取指定适配器类型的配置字段模式"""
    try:
        registry: IMRegistry = g.container.resolve(IMRegistry)
        try:
            config_class = registry.get_config_class(adapter_type)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404

        schema = config_class.model_json_schema(schema_generator=ConfigJsonSchema)
        return IMAdapterConfigSchema(configSchema=schema).model_dump()
    except Exception as e:
        return IMAdapterConfigSchema(error=str(e)).model_dump()
