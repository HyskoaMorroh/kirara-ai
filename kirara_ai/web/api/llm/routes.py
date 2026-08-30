from copy import deepcopy
from functools import wraps
import json
import os
from typing import Any

from pydantic import ValidationError
from quart import Blueprint, Response, g, jsonify, request

from kirara_ai.agent_runtime import AgentRegistry, RuntimeStatus
from kirara_ai.config.config_loader import CONFIG_FILE, ConfigLoader
from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.adapter import AutoDetectModelsProtocol
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.llm_registry import LLMBackendRegistry
from kirara_ai.llm.pricing import PriceCatalog, PriceCatalogConflictError, PriceVersion
from kirara_ai.logger import get_logger
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.scheduler import TaskScheduler
from kirara_ai.scheduler.model_catalog import normalize_detected_models
from kirara_ai.scheduler.scheduler import CONFIG_UPDATE_LOCK
from kirara_ai.web.api.llm.models import (LLMAdapterConfigSchema, LLMAdapterTypes, LLMBackendCreateRequest,
                                          LLMBackendInfo, LLMBackendList, LLMBackendListResponse, LLMBackendResponse,
                                          LLMBackendUpdateRequest, ModelConfigListResponse, WebUIChatRequest)
from kirara_ai.web.api.llm.webui_adapter import WebUIAdapter
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher

from ...auth.middleware import require_auth

llm_bp = Blueprint("llm", __name__)
logger = get_logger("WebServer.LLM")


_PRICE_VERSION_FIELDS = frozenset(
    {
        "version_id",
        "provider",
        "model",
        "effective_from",
        "currency",
        "input_per_million",
        "output_per_million",
        "cache_read_per_million",
        "cache_write_per_million",
    }
)


async def _pricing_json_object(
    allowed_fields: frozenset[str],
) -> tuple[dict[str, Any] | None, tuple[Response, int] | None]:
    payload = await request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (jsonify({"error": "Request body must be a JSON object"}), 400)
    if set(payload) - allowed_fields:
        return None, (jsonify({"error": "Request contains unknown fields"}), 400)
    return payload, None


def _pricing_expected_revision(payload: dict[str, Any]) -> int | None:
    value = payload.get("expected_revision")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected_revision must be a non-negative integer")
    return value


def _pricing_version(payload: object) -> PriceVersion:
    if not isinstance(payload, dict):
        raise ValueError("version must be a JSON object")
    if set(payload) - _PRICE_VERSION_FIELDS:
        raise ValueError("version contains unknown fields")
    return PriceVersion.model_validate(payload)


def _pricing_version_payload(version: PriceVersion) -> dict[str, Any]:
    return version.model_dump(mode="json")


def _pricing_catalog() -> PriceCatalog:
    catalog = g.container.resolve(PriceCatalog)
    if not isinstance(catalog, PriceCatalog):
        raise RuntimeError("price catalog is unavailable")
    return catalog


def _pricing_conflict(catalog: PriceCatalog, expected: int | None):
    try:
        current = catalog.refresh()
    except Exception:
        current = catalog.revision
    return jsonify(
        {
            "error": "Price catalog revision conflict",
            "code": "revision_conflict",
            "expected_revision": expected,
            "current_revision": current,
        }
    ), 409


def _audit_pricing_success(operation: str) -> None:
    """Record bounded identity metadata only after a successful mutation."""

    record: dict[str, Any] = {
        "component": "llm_pricing",
        "operation": operation,
        "outcome": "success",
    }
    principal = getattr(g, "auth_principal", None)
    if principal is not None:
        record.update(principal.audit_fields())
    try:
        lifecycle: ResourceLifecycleService = g.container.resolve(
            ResourceLifecycleService
        )
        lifecycle.append_runtime_audit(record)
    except Exception as error:
        # The catalog is already durable, so a sink outage must not turn a
        # successful operation into a misleading API failure.
        logger.warning("Pricing audit sink unavailable: {}", type(error).__name__)


def _pricing_internal_failure(error: Exception):
    logger.error("Price catalog operation failed: {}", type(error).__name__)
    return jsonify({"error": "Price catalog operation failed"}), 500


@llm_bp.route("/pricing", methods=["GET"])
@require_auth("llm.pricing.read")
async def list_pricing():
    try:
        catalog = _pricing_catalog()
        catalog.refresh()
        return jsonify(
            {
                "data": {
                    "revision": catalog.revision,
                    "versions": [
                        _pricing_version_payload(version)
                        for version in catalog.values()
                    ],
                    "backup_generations": list(catalog.backup_generations()),
                }
            }
        )
    except Exception as error:
        return _pricing_internal_failure(error)


@llm_bp.route("/pricing/<version_id>", methods=["GET"])
@require_auth("llm.pricing.read")
async def get_pricing(version_id: str):
    try:
        catalog = _pricing_catalog()
        catalog.refresh()
        version = next(
            (item for item in catalog.values() if item.version_id == version_id),
            None,
        )
        if version is None:
            return jsonify({"error": "Price version not found"}), 404
        return jsonify(
            {
                "data": {
                    "revision": catalog.revision,
                    "version": _pricing_version_payload(version),
                }
            }
        )
    except Exception as error:
        return _pricing_internal_failure(error)


@llm_bp.route("/pricing", methods=["POST"])
@require_auth("llm.pricing.manage")
async def create_pricing():
    expected_revision: int | None = None
    try:
        payload, failure = await _pricing_json_object(
            frozenset({"expected_revision", "version"})
        )
        if failure is not None:
            return failure
        assert payload is not None
        expected_revision = _pricing_expected_revision(payload)
        version = _pricing_version(payload.get("version"))
        catalog = _pricing_catalog()
        catalog.add(version, expected_revision=expected_revision)
        _audit_pricing_success("create")
        return jsonify(
            {
                "data": {
                    "revision": catalog.revision,
                    "version": _pricing_version_payload(version),
                }
            }
        ), 201
    except PriceCatalogConflictError:
        return _pricing_conflict(_pricing_catalog(), expected_revision)
    except (ValidationError, ValueError, TypeError) as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        return _pricing_internal_failure(error)


@llm_bp.route("/pricing/<version_id>", methods=["PUT"])
@require_auth("llm.pricing.manage")
async def update_pricing(version_id: str):
    expected_revision: int | None = None
    try:
        payload, failure = await _pricing_json_object(
            frozenset({"expected_revision", "version"})
        )
        if failure is not None:
            return failure
        assert payload is not None
        expected_revision = _pricing_expected_revision(payload)
        version = _pricing_version(payload.get("version"))
        if version.version_id != version_id:
            return jsonify({"error": "URL and body version IDs must match"}), 400
        catalog = _pricing_catalog()
        if not any(item.version_id == version_id for item in catalog.values()):
            return jsonify({"error": "Price version not found"}), 404
        catalog.update(version, expected_revision=expected_revision)
        _audit_pricing_success("update")
        return jsonify(
            {
                "data": {
                    "revision": catalog.revision,
                    "version": _pricing_version_payload(version),
                }
            }
        )
    except PriceCatalogConflictError:
        return _pricing_conflict(_pricing_catalog(), expected_revision)
    except KeyError:
        return jsonify({"error": "Price version not found"}), 404
    except (ValidationError, ValueError, TypeError) as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        return _pricing_internal_failure(error)


@llm_bp.route("/pricing/<version_id>", methods=["DELETE"])
@require_auth("llm.pricing.manage")
async def delete_pricing(version_id: str):
    expected_revision: int | None = None
    try:
        payload, failure = await _pricing_json_object(
            frozenset({"expected_revision", "confirmed"})
        )
        if failure is not None:
            return failure
        assert payload is not None
        expected_revision = _pricing_expected_revision(payload)
        if payload.get("confirmed") is not True:
            return jsonify({"error": "Deletion requires confirmed: true"}), 400
        catalog = _pricing_catalog()
        if not any(item.version_id == version_id for item in catalog.values()):
            return jsonify({"error": "Price version not found"}), 404
        catalog.remove(version_id, expected_revision=expected_revision)
        _audit_pricing_success("delete")
        return jsonify(
            {"data": {"revision": catalog.revision, "version_id": version_id}}
        )
    except PriceCatalogConflictError:
        return _pricing_conflict(_pricing_catalog(), expected_revision)
    except KeyError:
        return jsonify({"error": "Price version not found"}), 404
    except (ValidationError, ValueError, TypeError) as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        return _pricing_internal_failure(error)


@llm_bp.route("/pricing/export", methods=["GET"])
@require_auth("llm.pricing.read")
async def export_pricing():
    try:
        catalog = _pricing_catalog()
        catalog.refresh()
        body = json.dumps(
            catalog.export_document(), ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        response = Response(body, content_type="application/json")
        response.headers["Content-Disposition"] = (
            'attachment; filename="price-catalog.json"'
        )
        return response
    except Exception as error:
        return _pricing_internal_failure(error)


@llm_bp.route("/pricing/import", methods=["POST"])
@require_auth("llm.pricing.manage")
async def import_pricing():
    expected_revision: int | None = None
    try:
        payload, failure = await _pricing_json_object(
            frozenset({"expected_revision", "catalog"})
        )
        if failure is not None:
            return failure
        assert payload is not None
        expected_revision = _pricing_expected_revision(payload)
        catalog = _pricing_catalog()
        imported_count = catalog.import_document(
            payload.get("catalog"), expected_revision=expected_revision
        )
        _audit_pricing_success("import")
        return jsonify(
            {
                "data": {
                    "revision": catalog.revision,
                    "imported_count": imported_count,
                }
            }
        )
    except PriceCatalogConflictError:
        return _pricing_conflict(_pricing_catalog(), expected_revision)
    except (ValidationError, ValueError, TypeError) as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        return _pricing_internal_failure(error)


@llm_bp.route("/pricing/restore", methods=["POST"])
@require_auth("llm.pricing.manage")
async def restore_pricing():
    expected_revision: int | None = None
    try:
        payload, failure = await _pricing_json_object(
            frozenset({"expected_revision", "generation", "confirmed"})
        )
        if failure is not None:
            return failure
        assert payload is not None
        expected_revision = _pricing_expected_revision(payload)
        if payload.get("confirmed") is not True:
            return jsonify({"error": "Restore requires confirmed: true"}), 400
        generation = payload.get("generation")
        if generation is not None and (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
            or generation > 3
        ):
            return jsonify({"error": "generation must be between 1 and 3"}), 400
        catalog = _pricing_catalog()
        catalog.restore_backup(
            generation=generation, expected_revision=expected_revision
        )
        _audit_pricing_success("restore")
        return jsonify(
            {
                "data": {
                    "revision": catalog.revision,
                    "restored_generation": generation if generation is not None else 1,
                    "version_count": len(catalog.values()),
                }
            }
        )
    except PriceCatalogConflictError:
        return _pricing_conflict(_pricing_catalog(), expected_revision)
    except (ValidationError, ValueError, TypeError) as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        return _pricing_internal_failure(error)


@llm_bp.route("/chat", methods=["POST"])
@require_auth
async def webui_chat():
    """Route a WebUI message through the same dispatcher used by IM channels."""

    try:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        chat_request = WebUIChatRequest.model_validate(payload)

        if chat_request.chat_type == "group":
            sender = ChatSender.from_group_chat(
                user_id=chat_request.session_id,
                group_id=chat_request.group_id or "",
                display_name=chat_request.username,
            )
        else:
            sender = ChatSender.from_c2c_chat(
                user_id=chat_request.session_id,
                display_name=chat_request.username,
            )

        message = IMMessage(
            sender=sender,
            message_elements=[TextMessage(chat_request.message)],
        )
        adapter = WebUIAdapter(session_agent_id=chat_request.agent_id)
        dispatcher: WorkflowDispatcher = g.container.resolve(WorkflowDispatcher)
        result = await dispatcher.dispatch(adapter, message, require_agent=True)
        if result is None:
            return jsonify({"error": "No dispatch rule handled this message"}), 409

        context = result.context
        if context is None:
            from kirara_ai.agent_runtime import ChannelContext

            context = ChannelContext.from_message(adapter, message)

        if result.status is RuntimeStatus.FAILED:
            logger.warning(
                "WebUI Agent runtime failed with type {}",
                (result.error or {}).get("type", "RuntimeError"),
            )
            return jsonify({"error": "Agent runtime failed"}), 502

        return jsonify(
            {
                "status": result.status.value,
                "text": result.text,
                "agent_id": result.agent_id,
                "session_id": chat_request.session_id,
                "session_key": context.session_key,
                "confirmation_id": result.confirmation_id,
            }
        )
    except ValidationError as error:
        logger.debug("Rejected invalid WebUI chat request: {}", error.error_count())
        return jsonify({"error": "Invalid WebUI chat request"}), 400
    except LookupError as error:
        if str(error) == "No Agent is configured for this channel identity":
            return jsonify({"error": str(error)}), 409
        logger.warning("WebUI chat rejected: {}", error)
        return jsonify({"error": str(error)}), 400
    except ValueError as error:
        logger.warning("WebUI chat rejected: {}", error)
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        logger.opt(exception=error).error("WebUI Agent runtime dispatch failed")
        return jsonify({"error": "Agent runtime dispatch failed"}), 502


SENSITIVE_CONFIG_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "password",
        "secret",
        "credential",
        "token",
        "api_key",
        "apikey",
    }
)
SENSITIVE_CONFIG_SUFFIXES = ("_token", "_secret", "_password", "_credential")
NON_SECRET_TOKEN_KEYS = frozenset(
    {"max_tokens", "prompt_tokens", "completion_tokens", "total_tokens"}
)


def _is_sensitive_config_key(key: object) -> bool:
    normalized = str(key).strip().lower()
    if normalized in NON_SECRET_TOKEN_KEYS:
        return False
    return normalized in SENSITIVE_CONFIG_KEYS or normalized.endswith(
        SENSITIVE_CONFIG_SUFFIXES
    )


def _redact_sensitive_config(value: Any) -> Any:
    """Return an API-safe copy without provider credentials."""
    if isinstance(value, dict):
        return {
            key: "" if _is_sensitive_config_key(key) else _redact_sensitive_config(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_config(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive_config(item) for item in value)
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


def _backend_config(value: Any) -> LLMBackendConfig:
    """Build the persisted backend model without exposing its secrets."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return LLMBackendConfig.model_validate(value)


def _backend_info(backend: LLMBackendConfig) -> LLMBackendInfo:
    """Serialize a backend without dropping resilience settings or credentials safely."""
    payload = backend.model_dump()
    payload["config"] = _redact_sensitive_config(payload.get("config", {}))
    return LLMBackendInfo.model_validate(payload)


#: 供应商配置导入/导出的文档版本。导入时校验它，避免把未来格式静默按旧规则解释。
BACKEND_DOCUMENT_VERSION = 1


def _audit_backend_success(operation: str, *, backend_name: str) -> None:
    """Record who changed which provider configuration, without the credential.

    需求 21.1 要求供应商配置的导入/编辑「有校验、备份、恢复、冲突处理和审计记录」。
    定价接口一直有审计（`_audit_pricing_success`），供应商凭据这条路径却没有——
    而它恰恰是更敏感的一条：改错一个后端会让整个渠道停摆，且涉及凭据本身。
    这里只记录操作、后端名与主体摘要，绝不记录配置内容。
    """
    record: dict[str, Any] = {
        "component": "llm_backend",
        "operation": operation,
        "outcome": "success",
        "backend_name": backend_name,
    }
    principal = getattr(g, "auth_principal", None)
    if principal is not None:
        record.update(principal.audit_fields())
    try:
        lifecycle: ResourceLifecycleService = g.container.resolve(
            ResourceLifecycleService
        )
        lifecycle.append_runtime_audit(record)
    except Exception as error:
        # 配置已经落盘，审计沉降失败不能把一次成功操作报成失败。
        logger.warning("Backend audit sink unavailable: {}", type(error).__name__)


def _export_backend_document(config: GlobalConfig) -> dict[str, Any]:
    """Build a credential-free, re-importable provider configuration document.

    **凭据一律不导出。** 导出文件会被邮件转发、贴进工单、提交进仓库；
    带着 API Key 的「配置备份」是一次等待发生的泄露。因此敏感字段导出为空串，
    导入端把空串理解为「保留现有值」（与编辑接口同一约定），
    于是「导出 → 换机器导入 → 补填 Key」是完整可用的迁移路径。
    """
    backends: list[dict[str, Any]] = []
    for backend in config.llms.api_backends:
        payload = backend.model_dump()
        payload["config"] = _redact_sensitive_config(payload.get("config", {}))
        backends.append(payload)
    return {
        "document_version": BACKEND_DOCUMENT_VERSION,
        "backends": backends,
    }


def _validate_backend_document(document: Any) -> list[LLMBackendConfig]:
    """Validate an imported document and return parsed backends.

    校验三件事，任何一条不满足就整份拒绝——半导入的配置比不导入更难修复：
    1. 文档版本已知；
    2. 每个后端都能通过 `LLMBackendConfig` 的完整校验（含容错参数的边界）；
    3. 名称唯一且非空——重名会让「加载哪一个」变成未定义行为。
    """
    if not isinstance(document, dict):
        raise ValueError("import document must be an object")
    version = document.get("document_version")
    if version != BACKEND_DOCUMENT_VERSION:
        raise ValueError(
            f"unsupported document_version: {version!r}; "
            f"expected {BACKEND_DOCUMENT_VERSION}"
        )
    raw_backends = document.get("backends")
    if not isinstance(raw_backends, list) or not raw_backends:
        raise ValueError("import document must contain a non-empty backends list")

    parsed: list[LLMBackendConfig] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_backends):
        if not isinstance(item, dict):
            raise ValueError(f"backends[{index}] must be an object")
        backend = LLMBackendConfig.model_validate(item)
        name = str(backend.name).strip()
        if not name:
            raise ValueError(f"backends[{index}] has an empty name")
        if name in seen:
            raise ValueError(f"duplicate backend name in document: {name}")
        seen.add(name)
        parsed.append(backend)
    return parsed


def serialize_config_update(func):
    """串行化会修改 LLM 配置的请求，避免与后台模型刷新交错写入。"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        async with CONFIG_UPDATE_LOCK:
            return await func(*args, **kwargs)

    return wrapper


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
            backends.append(_backend_info(backend))
        return LLMBackendListResponse(
            data=LLMBackendList(backends=backends)
        ).model_dump()
    except Exception as e:
        logger.opt(exception=e).error("Failed to list backends")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/backends/export", methods=["GET"])
@require_auth("llm.backends.read")
async def export_backends():
    """Download a credential-free provider configuration document.

    定价目录早就有导入/导出，供应商配置却没有——迁移一台部署只能手抄十几个
    后端的容错参数。这里补上，但**不导出凭据**：导出文件会被转发、贴工单、
    提交进仓库，带 API Key 的「配置备份」是一次等待发生的泄露。
    """
    try:
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        body = json.dumps(
            _export_backend_document(config),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        response = Response(body, content_type="application/json")
        response.headers["Content-Disposition"] = (
            'attachment; filename="llm-backends.json"'
        )
        return response
    except Exception as error:
        logger.opt(exception=error).error("Failed to export backends")
        return jsonify({"error": "Failed to export backends"}), 500


@llm_bp.route("/backends/import", methods=["POST"])
@require_auth("llm.backends.manage")
async def import_backends():
    """Import a provider configuration document with validation and a backup.

    三条不可协商的行为：

    - **整份校验后再落盘**。任何一个后端不合法就整份拒绝：半导入的配置比不导入
      更难修复，而且会让「哪些生效了」变成需要逐个点开确认的问题。
    - **空的敏感字段表示保留现有值**，与编辑接口同一约定。这样「导出 → 换机器
      导入」不会把目标机器上已经填好的 Key 清空。
    - **冲突要显式选择**。同名后端默认拒绝；只有 `overwrite: true` 才替换，
      并在审计里记下这次覆盖。
    """
    try:
        payload = await request.get_json()
        if not isinstance(payload, dict):
            return jsonify({"error": "request body must be an object"}), 400
        unknown = set(payload) - {"document", "overwrite"}
        if unknown:
            return jsonify({"error": f"unexpected fields: {sorted(unknown)}"}), 400

        overwrite = payload.get("overwrite", False)
        if not isinstance(overwrite, bool):
            return jsonify({"error": "overwrite must be a boolean"}), 400

        incoming = _validate_backend_document(payload.get("document"))

        config: GlobalConfig = g.container.resolve(GlobalConfig)
        existing = {backend.name: backend for backend in config.llms.api_backends}
        conflicts = [backend.name for backend in incoming if backend.name in existing]
        if conflicts and not overwrite:
            return (
                jsonify(
                    {
                        "error": "backend name conflict",
                        "conflicts": sorted(conflicts),
                        "hint": "resend with overwrite: true to replace them",
                    }
                ),
                409,
            )

        manager: LLMManager = g.container.resolve(LLMManager)
        imported = 0
        for backend in incoming:
            current = existing.get(backend.name)
            if current is not None:
                # 空白凭据表示「保留现有」：导入不该清空目标机器上已填好的 Key。
                merged = _restore_unchanged_secrets(
                    backend.model_dump(), current.model_dump()
                )
                replacement = LLMBackendConfig.model_validate(merged)
                config.llms.api_backends[
                    config.llms.api_backends.index(current)
                ] = replacement
            else:
                config.llms.api_backends.append(backend)
            imported += 1

        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)

        # 落盘之后再加载：加载失败不该留下一份「已保存但没生效」的配置差异。
        for backend in incoming:
            if backend.enable:
                try:
                    manager.load_backend(backend.name)
                except Exception as error:
                    logger.warning(
                        "Imported backend {} failed to load: {}",
                        backend.name,
                        type(error).__name__,
                    )

        _audit_backend_success(
            "import_overwrite" if conflicts else "import",
            backend_name=",".join(sorted(backend.name for backend in incoming))[:256],
        )
        return jsonify(
            {
                "data": {
                    "imported_count": imported,
                    "overwritten": sorted(conflicts),
                }
            }
        )
    except (ValidationError, ValueError, TypeError) as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        logger.opt(exception=error).error("Failed to import backends")
        return jsonify({"error": "Failed to import backends"}), 500


@llm_bp.route("/backends/restore", methods=["POST"])
@require_auth("llm.backends.manage")
async def restore_backends():
    """Roll the provider list back to the copy taken before the last write.

    `save_config_with_backup` 每次写入前都会留一份 `config.yaml.bak`，
    但一直没有任何接口能把它取回来——改错一个后端之后只能登服务器手工编辑。
    定价目录早有 `/pricing/restore`，供应商配置这条更敏感的路径反而没有。

    三条刻意的设计：

    - **只回滚 `llms.api_backends`**。`config.yaml` 是整个项目的配置文件，
      把 `.bak` 整份写回去会把用户同一时间改过的 Web 端口、IM 适配器、
      工作流一起退回上一个状态——那是「回滚全部设置」，不是「恢复供应商配置」。
    - **要求显式确认**。恢复会丢掉最后一次写入的供应商改动，和删除同级。
    - **备份不存在返回 404**，不是 500：没有可恢复的东西是一种正常状态。
    """
    try:
        payload = await request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify({"error": "request body must be an object"}), 400
        unknown = set(payload) - {"confirmed"}
        if unknown:
            return jsonify({"error": f"unexpected fields: {sorted(unknown)}"}), 400
        if payload.get("confirmed") is not True:
            return jsonify({"error": "Restore requires confirmed: true"}), 400

        backup_path = f"{CONFIG_FILE}.bak"
        if not os.path.isfile(backup_path):
            return (
                jsonify(
                    {
                        "error": "no provider configuration backup available",
                        "hint": "a backup appears after the first configuration write",
                    }
                ),
                404,
            )

        try:
            backup_config = ConfigLoader.load_config(backup_path, GlobalConfig)
        except (ValueError, RuntimeError) as error:
            logger.warning("Provider backup is unusable: {}", type(error).__name__)
            return jsonify({"error": "provider configuration backup is unreadable"}), 422

        config: GlobalConfig = g.container.resolve(GlobalConfig)
        manager: LLMManager = g.container.resolve(LLMManager)

        # 先卸载当前已加载的后端，再换清单：顺序颠倒会让被移除的后端留在
        # manager 里继续接请求，指向一份已经不存在的配置。
        for backend in list(config.llms.api_backends):
            if backend.name in getattr(manager, "backends", {}):
                await manager.unload_backend(backend.name)

        restored = [
            LLMBackendConfig.model_validate(backend.model_dump())
            for backend in backup_config.llms.api_backends
        ]
        config.llms.api_backends = restored

        ConfigLoader.save_config(CONFIG_FILE, config)

        loaded = 0
        for backend in restored:
            if not backend.enable:
                continue
            try:
                manager.load_backend(backend.name)
                loaded += 1
            except Exception as error:
                logger.warning(
                    "Restored backend {} failed to load: {}",
                    backend.name,
                    type(error).__name__,
                )

        _audit_backend_success(
            "restore",
            backend_name=",".join(backend.name for backend in restored)[:256],
        )
        return jsonify(
            {
                "data": {
                    "restored_count": len(restored),
                    "loaded_count": loaded,
                    "backends": [backend.name for backend in restored],
                }
            }
        )
    except Exception as error:
        logger.opt(exception=error).error("Failed to restore backends")
        return jsonify({"error": "Failed to restore backends"}), 500


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

        return LLMBackendResponse(data=_backend_info(backend)).model_dump()
    except Exception as e:
        logger.opt(exception=e).error("Failed to get backend")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/resilience/status", methods=["GET"])
@require_auth
async def get_resilience_status():
    """Return sanitized provider health for the operational status view."""
    try:
        manager: LLMManager = g.container.resolve(LLMManager)
        return jsonify({"data": manager.get_resilience_status()})
    except Exception as e:
        logger.opt(exception=e).error("Failed to get LLM resilience status")
        return jsonify({"error": "Failed to get LLM resilience status"}), 500


@llm_bp.route("/backends", methods=["POST"])
@require_auth
@serialize_config_update
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
        backend = _backend_config(request_data)

        # 添加到配置中
        config.llms.api_backends.append(backend)

        # 如果启用则加载后端
        if backend.enable:
            manager.load_backend(backend.name)

        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
        _audit_backend_success("create", backend_name=backend.name)
        return LLMBackendResponse(data=_backend_info(backend)).model_dump()
    except Exception as e:
        logger.opt(exception=e).error("Failed to create backend")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/backends/<backend_name>", methods=["PUT"])
@require_auth
@serialize_config_update
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

        original_backend = config.llms.api_backends[backend_index]
        # 只覆盖客户端**真的提交过**的字段。
        #
        # `model_dump()` 无条件产出全部字段（缺的用模型默认值补齐），于是任何
        # 前端表单不认识的键都会在一次无关的编辑里被静默重置为出厂值——
        # 用户在 config.yaml 里开的 `hide_ai_attribution` 会在他改一个超时数字时
        # 被关掉，而界面上没有任何地方显示过这个字段，所以他既看不到它被关，
        # 也无从把症状与那次编辑联系起来。
        #
        # `exclude_unset=True` 让「没发这个键」与「发了 false」成为两件事：
        # 前者保留原值，后者照常关掉。这一处修好覆盖所有现有与将来新增的字段，
        # 比逐个字段补前端控件更可靠——漏一个字段就等于留一个静默重置。
        submitted = original_backend.model_dump()
        submitted.update(request_data.model_dump(exclude_unset=True))
        if request_data.adapter == original_backend.adapter:
            submitted["config"] = _restore_unchanged_secrets(
                submitted.get("config", {}), original_backend.config
            )
        # Keep the real configuration in memory and on disk; only the response
        # serializer is allowed to redact it.
        updated_backend = _backend_config(submitted)
        backend_was_loaded = backend_name in manager.backends

        # 如果原后端已启用，先卸载
        if original_backend.enable and backend_was_loaded:
            await manager.unload_backend(backend_name)

        # 更新配置
        config.llms.api_backends[backend_index] = updated_backend

        try:
            # 如果新配置启用则加载后端
            if updated_backend.enable:
                manager.load_backend(updated_backend.name)
            ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
        except Exception:
            try:
                if updated_backend.name in manager.backends:
                    await manager.unload_backend(updated_backend.name)

                config.llms.api_backends[backend_index] = original_backend

                if (
                    original_backend.enable
                    and backend_was_loaded
                    and backend_name not in manager.backends
                ):
                    manager.load_backend(backend_name)
            except Exception as rollback_error:
                logger.opt(exception=rollback_error).error(
                    f"Failed to roll back backend {backend_name} after update failure"
                )
            raise

        _audit_backend_success("update", backend_name=updated_backend.name)
        return LLMBackendResponse(data=_backend_info(updated_backend)).model_dump()
    except Exception as e:
        logger.opt(exception=e).error("Failed to update backend")
        return jsonify({"error": str(e)}), 500


@llm_bp.route("/backends/<backend_name>", methods=["DELETE"])
@require_auth
@serialize_config_update
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

        _audit_backend_success("delete", backend_name=backend_name)

        return LLMBackendResponse(data=_backend_info(deleted_backend)).model_dump()
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
        # 自动检测与后台定时任务走同一份规范化逻辑：只返回当前可发现的
        # 模型目录，兼容旧适配器返回的字符串 ID，不触碰任何工作流模型槽位。
        models = normalize_detected_models(await adapter.auto_detect_models())

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
@serialize_config_update
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
