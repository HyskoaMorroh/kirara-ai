from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Awaitable, Callable

from quart import Blueprint, g, jsonify, request

from kirara_ai.agent_runtime import AgentRegistry
from kirara_ai.logger import get_logger
from kirara_ai.plugin_manager.resource_lifecycle import (
    MAX_ARCHIVE_SIZE_BYTES,
    RUNTIME_OVERRIDE_KEYS,
    ResourceLifecycleService,
    ResourceStateError,
    ResourceValidationError,
)
from kirara_ai.plugin_manager.resource_sources import ResourceSourceError, ResourceSourceService
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogError, ResourceCatalogService
from kirara_ai.plugin_manager.system_dependencies import (
    DependencyInstallConfirmationRequired,
    DependencyInstallUnsupported,
    DependencyNotFoundError,
    DependencyTaskStateError,
    SystemDependencyError,
    SystemDependencyService,
)
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.web.api.mcp.models import REDACTED_SECRET
from kirara_ai.web.auth.middleware import require_auth, require_creator


resource_bp = Blueprint("resource", __name__)
logger = get_logger("Web.Resource")
MAX_RESOURCE_UPLOAD_SIZE = MAX_ARCHIVE_SIZE_BYTES

#: 搜索关键词的上界。无界关键词会让每条资源都做一次超长子串匹配，
#: 而一个真实的搜索词不会有这么长。
_MAX_SEARCH_KEYWORD_LENGTH = 200


def _service() -> ResourceLifecycleService:
    return g.container.resolve(ResourceLifecycleService)


def _sources() -> ResourceSourceService:
    return g.container.resolve(ResourceSourceService)


def _catalog() -> ResourceCatalogService:
    return g.container.resolve(ResourceCatalogService)


def _dependencies() -> SystemDependencyService:
    return g.container.resolve(SystemDependencyService)


def _error(message: str, status_code: int):
    return jsonify({"error": message}), status_code


def _runtime_status(resource: dict[str, Any]) -> dict[str, Any]:
    """Project ephemeral runtime state without mutating lifecycle data."""

    if resource.get("type") == "mcp":
        server_id = str(resource.get("resource_id", "")).removeprefix("mcp.")
        if server_id and g.container.has(MCPServerManager):
            return g.container.resolve(MCPServerManager).get_runtime_status(server_id)
        return {
            "status": "stopped",
            "running": False,
            "failed": False,
            "last_error": None,
            "last_checked_at": None,
        }

    enabled = resource.get("enabled") is True
    return {
        "status": "running" if enabled else "stopped",
        "running": enabled,
        "failed": False,
        "last_error": None,
        "last_checked_at": None,
    }


def _binding_visibility(resource: dict[str, Any]) -> dict[str, Any]:
    """Report whether any Agent actually binds this resource.

    需求 22.3 的一个隐性缺口：装好并启用之后界面显示「已启用」，但一个 Skill /
    Prompt / MCP 资源只有在被**绑定到某个 Agent** 之后才会进入 LLM 请求
    （`executor._build_messages` 遍历 `agent.*_bindings`）。没有绑定时状态是
    「已启用」而实际效果是零——用户看到「已启用」，得到「什么都没变」，
    然后去怀疑模型或提示词。这不是功能缺失，是状态显示与实际效果不一致，
    而界面上没有任何地方在说「它还差一步」。

    拿不到 Agent 注册表时**返回空字典**而不是 ``in_effect: False``：
    后者是一个论断，在读不到绑定关系的部署里给出它等于告诉用户
    「你的 Skill 没生效」，而实际情况是「我们不知道」。
    """

    if not g.container.has(AgentRegistry):
        return {}
    resource_id = str(resource.get("resource_id", ""))
    if not resource_id:
        return {}
    try:
        agents = g.container.resolve(AgentRegistry).list()
    except Exception:  # noqa: BLE001 - 观测字段不得让整个列表打不开
        return {}

    bound_agent_ids: list[str] = []
    has_enabled_binding = False
    for agent in agents:
        bindings = [
            binding
            for binding in agent.resource_bindings
            if binding.resource_id == resource_id
        ]
        if not bindings:
            continue
        bound_agent_ids.append(agent.agent_id)
        # Agent 本身被停用时它的绑定不会进任何请求。
        if agent.enabled and any(binding.enabled for binding in bindings):
            has_enabled_binding = True

    return {
        # 「已绑定」与「生效」分开：绑定关系解释了「为什么改这个 Agent 会影响
        # 这个资源」，即使那条绑定当前被停用，也值得显示。
        "bound_agent_ids": sorted(bound_agent_ids),
        "in_effect": bool(resource.get("enabled") is True and has_enabled_binding),
    }


def _redact_runtime_overrides(resource: dict[str, Any]) -> None:
    """把运行时覆盖里的凭据换成掩码，只留键名。

    `runtime_overrides.env` / `.headers` 装的是 MCP 服务器的凭据。它们出现在
    **每一次**资源响应里（列表、详情、启用、写入回显都走 `_resource_response`），
    所以不遮的后果不是「某个接口漏了」而是「一个只想看清单的请求把全部
    MCP 凭据取回浏览器」。

    与 `GET /mcp/servers` 的 `_redact_transport` 同一条规则、同一个掩码：
    两个接口回的是同一份东西，一个遮一个不遮等于遮了也没用。
    键名保留，否则界面看不出配过什么。

    写回掩码不会污染存储：这里改的是 `deepcopy` 出来的响应副本。
    而 `set_runtime_overrides` 的 `env` 按键合并、把掩码当作真值写入是可能的——
    因此那一层的调用方（前端）不回传未修改的键，与 MCP 编辑表单同一约定。
    """

    overrides = resource.get("runtime_overrides")
    if not isinstance(overrides, dict):
        return
    for key in ("env", "headers"):
        values = overrides.get(key)
        if isinstance(values, dict):
            overrides[key] = {str(name): REDACTED_SECRET for name in values}


def _resource_response(resource: Any) -> Any:
    """Add transient runtime and VPS readiness fields to lifecycle objects."""

    if not isinstance(resource, dict) or not {"resource_id", "type"}.issubset(resource):
        return resource
    projected = deepcopy(resource)
    projected = _catalog().project_dependencies(projected)
    projected.update(_runtime_status(projected))
    projected.update(_binding_visibility(projected))
    _redact_runtime_overrides(projected)
    return projected


def _resource_list_response(resources: Any) -> Any:
    if not isinstance(resources, list):
        return resources
    return [_resource_response(resource) for resource in resources]


def _lifecycle_error(error: Exception):
    if isinstance(error, (ResourceValidationError, ResourceSourceError, ResourceCatalogError)):
        return _error(str(error), 400)
    if isinstance(error, DependencyNotFoundError):
        return _error(str(error), 404)
    if isinstance(
        error,
        (
            DependencyInstallConfirmationRequired,
            DependencyInstallUnsupported,
            DependencyTaskStateError,
        ),
    ):
        return _error(str(error), 409)
    if isinstance(error, SystemDependencyError):
        return _error(str(error), 400)
    if isinstance(error, ResourceStateError):
        status_code = 404 if str(error) == "resource is not installed" else 409
        return _error(str(error), status_code)
    logger.opt(exception=error).error("Resource API request failed")
    return _error("resource operation failed", 500)


async def _json_object() -> dict[str, Any]:
    payload = await request.get_json(silent=True)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ResourceValidationError("request body must be a JSON object")
    return payload


async def _strict_json_object(allowed_fields: set[str]) -> dict[str, Any]:
    payload = await _json_object()
    unexpected = set(payload) - allowed_fields
    if unexpected:
        raise ResourceValidationError("request body contains unsupported fields")
    return payload


async def _save_uploaded_archive():
    if request.content_length and request.content_length > MAX_RESOURCE_UPLOAD_SIZE:
        return None, _error("resource archive exceeds the maximum size", 413)

    files = await request.files
    uploaded_file = files.get("resource")
    if uploaded_file is None or not uploaded_file.filename:
        return None, _error("resource archive is required", 400)

    lifecycle = _service()
    archive_path = lifecycle.imports_path / f"upload-{uuid.uuid4().hex}.zip"
    try:
        await uploaded_file.save(archive_path)
        if archive_path.stat().st_size > MAX_RESOURCE_UPLOAD_SIZE:
            archive_path.unlink(missing_ok=True)
            return None, _error("resource archive exceeds the maximum size", 413)
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path, None


async def _save_import_archive():
    """Stage an offline import below the server-managed resource directory."""

    if request.content_length and request.content_length > MAX_RESOURCE_UPLOAD_SIZE:
        return None, _error("resource archive exceeds the maximum size", 413)

    files = await request.files
    uploaded_file = files.get("resource")
    if uploaded_file is None or not uploaded_file.filename:
        return None, _error("resource archive is required", 400)

    lifecycle = _service()
    archive_path = lifecycle.imports_path / f"upload-{uuid.uuid4().hex}.zip"
    try:
        await uploaded_file.save(archive_path)
        if archive_path.stat().st_size > MAX_RESOURCE_UPLOAD_SIZE:
            archive_path.unlink(missing_ok=True)
            return None, _error("resource archive exceeds the maximum size", 413)
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path, None


async def _with_uploaded_archive(
    operation: Callable[[Path], dict[str, Any]],
    *,
    success_status: int,
):
    archive_path, upload_error = await _save_uploaded_archive()
    if upload_error is not None:
        return upload_error
    try:
        return jsonify(_resource_response(operation(archive_path))), success_status
    except Exception as error:
        return _lifecycle_error(error)
    finally:
        archive_path.unlink(missing_ok=True)


async def _with_import_archive():
    archive_path, upload_error = await _save_import_archive()
    if upload_error is not None:
        return upload_error
    try:
        return jsonify(_resource_response(_service().import_archive(archive_path))), 201
    except Exception as error:
        return _lifecycle_error(error)
    finally:
        archive_path.unlink(missing_ok=True)


async def _state_change(
    operation: Callable[[dict[str, Any]], dict[str, Any]],
):
    try:
        return jsonify(_resource_response(operation(await _json_object())))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/dependencies", methods=["GET"])
@require_auth("resources.read")
async def list_system_dependencies():
    try:
        return jsonify(_dependencies().list_dependencies())
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/dependencies/<dependency_id>", methods=["GET"])
@require_auth("resources.read")
async def get_system_dependency(dependency_id: str):
    try:
        return jsonify(_dependencies().get_dependency(dependency_id))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/dependencies/<dependency_id>/probe", methods=["POST"])
# 探测同样会在服务器上**执行**登记的 argv（例如 `agent-browser doctor`、
# `rtk --version`），只是不安装。既然是执行命令，就与 install 同一边界。
@require_creator("resources.manage")
async def probe_system_dependency(dependency_id: str):
    try:
        await _strict_json_object(set())
        result = await asyncio.to_thread(_dependencies().probe, dependency_id)
        return jsonify(result)
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/dependencies/<dependency_id>/install", methods=["POST"])
# 安装依赖会在服务器上执行命令：仅项目创建者可用（需求 10）。
@require_creator("resources.manage")
async def install_system_dependency(dependency_id: str):
    try:
        payload = await _strict_json_object({"confirmed"})
        if payload.get("confirmed") is not True:
            raise DependencyInstallConfirmationRequired(
                "dependency installation requires confirmation"
            )
        return jsonify(
            _dependencies().install(dependency_id, confirmed=True)
        ), 202
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/dependency-tasks", methods=["GET"])
@require_auth("resources.read")
async def list_dependency_tasks():
    try:
        return jsonify(
            _dependencies().list_tasks(
                dependency_id=request.args.get("dependency_id")
            )
        )
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/dependency-tasks/<task_id>", methods=["GET"])
@require_auth("resources.read")
async def get_dependency_task(task_id: str):
    try:
        return jsonify(_dependencies().get_task(task_id))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/dependency-tasks/<task_id>/retry", methods=["POST"])
# 重试等于再执行一次安装命令，同样限创建者。
@require_creator("resources.manage")
async def retry_dependency_task(task_id: str):
    try:
        payload = await _strict_json_object({"confirmed"})
        if payload.get("confirmed") is not True:
            raise DependencyInstallConfirmationRequired(
                "dependency installation requires confirmation"
            )
        return jsonify(
            _dependencies().retry_task(task_id, confirmed=True)
        ), 202
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/dependency-tasks/<task_id>/cancel", methods=["POST"])
# 取消会终止创建者启动的进程，不该由其他使用者触发。
@require_creator("resources.manage")
async def cancel_dependency_task(task_id: str):
    try:
        await _strict_json_object(set())
        return jsonify(_dependencies().cancel_task(task_id))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/repositories", methods=["GET"])
@require_auth("resources.read")
async def list_repositories():
    try:
        return jsonify(_sources().list_repositories())
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/repositories", methods=["POST"])
# 新增仓库会写 registry.json，并把一个外部来源列入「可从这里安装」——
# 属于修改服务器内容（需求 10）。
@require_creator("resources.manage")
async def add_repository():
    try:
        payload = await _json_object()
        return jsonify(
            _sources().add_repository(
                payload.get("owner"), payload.get("name"), payload.get("branch", "main")
            )
        ), 201
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/repositories/<owner>/<name>/<branch>/enabled", methods=["POST"])
# 启停仓库同样写 registry.json，并改变「哪些来源可被安装」。
@require_creator("resources.manage")
async def set_repository_enabled(owner: str, name: str, branch: str):
    try:
        payload = await _json_object()
        if not isinstance(payload.get("enabled"), bool):
            raise ResourceSourceError("repository enabled must be boolean")
        return jsonify(_sources().set_repository_enabled(owner, name, branch, payload["enabled"]))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/repositories/<owner>/<name>/<branch>", methods=["DELETE"])
# 与启停同一边界（写 `registry.json`，改变「哪些外部来源可被安装」），
# 但删除不可逆，因此额外要求显式确认——与卸载资源同一口径。
@require_creator("resources.manage")
async def remove_repository(owner: str, name: str, branch: str):
    """摘掉一条仓库来源登记。

    此前只有「登记」与「启停」，没有任何删除路径：一个拼错的坐标会永久留在
    `registry.json` 里，可以停用但去不掉，仓库表上永远多一行说明不了任何事的
    死项，想清掉只能登服务器手改 JSON。

    **不动已装资源**：从那个仓库装过的 Skill 已经独立成包（有自己的清单与摘要），
    一起删掉等于把「不再从这里拉新的」变成「把装过的都毁掉」。
    """
    try:
        payload = await _strict_json_object({"confirmed"})
        if payload.get("confirmed") is not True:
            raise ResourceValidationError("repository removal requires confirmation")
        return jsonify(_sources().remove_repository(owner, name, branch))
    except KeyError:
        # 「没有这个仓库」是客户端问题，不是服务器故障；静默成功会让一个拼错的
        # 删除请求看起来和真的删掉一样。
        return _error(f"repository '{owner}/{name}@{branch}' is not registered", 404)
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/repositories/<owner>/<name>/<branch>/discover", methods=["GET"])
@require_auth("resources.read")
async def discover_repository(owner: str, name: str, branch: str):
    try:
        discovered = await asyncio.to_thread(
            _sources().discover_repository, owner, name, branch
        )
        return jsonify(discovered)
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/skills-sh/search", methods=["GET"])
@require_auth("resources.read")
async def search_skills_sh():
    try:
        results = await asyncio.to_thread(
            _sources().search_skills,
                request.args.get("q", ""),
                limit=int(request.args.get("limit", 20)),
                offset=int(request.args.get("offset", 0)),
        )
        return jsonify(results)
    except (TypeError, ValueError):
        return _error("search pagination values must be integers", 400)
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/catalog/search", methods=["GET"])
@require_auth("resources.read")
async def search_catalog():
    try:
        results = await asyncio.to_thread(
            _catalog().search,
                request.args.get("type"),
                request.args.get("q", ""),
                limit=int(request.args.get("limit", 20)),
                offset=int(request.args.get("offset", 0)),
        )
        return jsonify(results)
    except (TypeError, ValueError):
        return _error("catalog pagination values must be integers", 400)
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/catalog/<path:catalog_id>", methods=["GET"])
@require_auth("resources.read")
async def get_catalog_item(catalog_id: str):
    try:
        return jsonify(_catalog().get(catalog_id))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/catalog/install", methods=["POST"])
# 目录安装会把资源解包到服务器磁盘（需求 10）。
@require_creator("resources.manage")
async def install_catalog_item():
    try:
        payload = await _json_object()
        catalog_id = payload.get("catalog_id")
        if not isinstance(catalog_id, str) or not catalog_id.strip():
            raise ResourceCatalogError("catalog_id is required")
        installed = await asyncio.to_thread(
            _catalog().install,
            catalog_id.strip(),
            branch=payload.get("branch"),
        )
        return jsonify(_resource_response(installed)), 201
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/remote-install", methods=["POST"])
# 远程安装会从外部仓库拉取内容并落到服务器磁盘。
@require_creator("resources.manage")
async def install_remote_skill():
    try:
        # 逐个键读取，不做 `**payload` splat。
        #
        # 此前这里是 `install_skill(**payload)`：多带一个键就抛 `TypeError`，
        # 被 `_lifecycle_error` 变成不带信息的 500；少带 `directory` 同样是 500
        # 而不是 400。更要紧的是 `install_skill` 将来任何一个关键字参数都会
        # 变成远程可设——同级的目录安装路由（`install_catalog_item`）一直是
        # 逐键读取的，只有这一条没跟上。
        payload = await _strict_json_object(
            {"owner", "name", "branch", "directory", "source_key"}
        )
        owner = payload.get("owner")
        name = payload.get("name")
        directory = payload.get("directory")
        for label, value in (("owner", owner), ("name", name), ("directory", directory)):
            if not isinstance(value, str) or not value.strip():
                raise ResourceValidationError(f"{label} is required")
        branch = payload.get("branch")
        if branch is not None and not isinstance(branch, str):
            raise ResourceValidationError("branch must be a string")
        source_key = payload.get("source_key")
        if source_key is not None and not isinstance(source_key, str):
            raise ResourceValidationError("source_key must be a string")

        installed = await asyncio.to_thread(
            _sources().install_skill,
            owner=owner.strip(),
            name=name.strip(),
            branch=branch,
            directory=directory.strip(),
            source_key=source_key,
        )
        return jsonify(_resource_response(installed)), 201
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/updates", methods=["GET"])
@require_auth("resources.read")
async def check_resource_updates():
    try:
        results = await asyncio.to_thread(
            _sources().check_updates, request.args.get("resource_id")
        )
        return jsonify(results)
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/backups", methods=["GET"])
@require_auth("resources.read")
async def list_backups():
    try:
        return jsonify(_service().list_backups(request.args.get("resource_id")))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/backups/<backup_id>/restore", methods=["POST"])
# 从备份恢复会把文件写回安装目录，属于修改服务器内容（需求 10）。
@require_creator("resources.manage")
async def restore_backup(backup_id: str):
    return await _state_change(
        lambda payload: _service().restore_backup(
            backup_id, confirmed=payload.get("confirmed") is True
        )
    )


@resource_bp.route("/backups/<backup_id>", methods=["DELETE"])
# 删除备份是不可逆的磁盘删除操作。
@require_creator("resources.manage")
async def delete_backup(backup_id: str):
    return await _state_change(
        lambda payload: _service().delete_backup(
            backup_id, confirmed=payload.get("confirmed") is True
        )
    )


# Static routes are declared before the resource ID route to keep /audit explicit.
@resource_bp.route("/audit", methods=["GET"])
@require_auth("resources.read")
async def list_audit():
    try:
        offset = int(request.args.get("offset", 0))
        limit = int(request.args.get("limit", 50))
        resource_id = request.args.get("resource_id")
        correlation_id = request.args.get("correlation_id")
        component = request.args.get("component")
        event = request.args.get("event")
        operation = request.args.get("operation")
        outcome = request.args.get("outcome") or request.args.get("result")
        status = request.args.get("status")
        agent_id = request.args.get("agent_id")
        model_id = request.args.get("model_id")
        server = request.args.get("server")
        return jsonify(
            _service().list_audit(
                offset=offset,
                limit=limit,
                resource_id=resource_id,
                correlation_id=correlation_id,
                component=component,
                event=event,
                operation=operation,
                outcome=outcome,
                status=status,
                agent_id=agent_id,
                model_id=model_id,
                server=server,
            )
        )
    except (TypeError, ValueError):
        return _error("audit pagination values must be integers", 400)
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/storage", methods=["GET"])
@require_auth("resources.read")
async def get_storage_status():
    try:
        return jsonify(_service().get_storage_status())
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("", methods=["GET"])
@require_auth("resources.read")
async def list_resources():
    """列出已安装资源，可按类型与关键词过滤。

    `query` 的匹配面包含**正文**（仅纯文本类型），这是需求 10 点名的
    「按名称、描述或内容检索」。过滤在服务器侧做而不是把正文发给浏览器自己筛：
    正文可能有几十 KB 且包含用户写进去的规则，而 `read_entry` 每次都要重新校验
    摘要——让列表接口顺带返回它，等于把一次列表请求变成 N 次全文件哈希，
    并把一个只想看清单的请求变成一次全部正文的下载。响应里仍然不含正文。
    """
    try:
        keyword = request.args.get("query")
        if keyword is not None and len(keyword) > _MAX_SEARCH_KEYWORD_LENGTH:
            # 无界关键词会让每条资源都做一次超长子串匹配。
            raise ResourceValidationError("search keyword is too long")
        service = _service()
        resources = (
            service.search_resources(keyword, resource_type=request.args.get("type"))
            if keyword is not None
            else service.list_resources(request.args.get("type"))
        )
        return jsonify(_resource_list_response(resources))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("", methods=["POST"])
# 上传 ZIP 安装会把归档内容解包到服务器磁盘（需求 10）。
@require_creator("resources.manage")
async def install_resource():
    return await _with_uploaded_archive(
        _service().install_archive,
        success_status=201,
    )


@resource_bp.route("/documents", methods=["POST"])
# 从纯文本创建资源同样写服务器磁盘，与上传 ZIP 安装同一边界（需求 10）。
@require_creator("resources.manage")
async def author_resource_document():
    """从一段纯文本创建 prompt / memory / session 资源。

    需求 10 点名「Claude 提示词管理」，而提示词这个类型的**全部内容就是正文**：
    没有可执行文件、没有依赖、没有外部来源。此前唯一的写入路径是上传一个手工
    打包的 ZIP——用户得自己写 `manifest.json` 的八个必填字段、按
    `path:size:sha256` 逐行拼接再哈希算出 `content_sha256`。
    要求为一段纯文本走这一遍，等于把这个类型最主要的用法排除在产品之外。

    打包与摘要在服务器侧完成，走的是与内置目录条目完全相同的那条路，
    因此落盘后与一条内置提示词同形，`read_entry` 的摘要校验照常生效。
    装完保持停用并需要确认，与其他三条安装路径一致——提示词会进系统提示词、
    改变每一轮回复，「保存即生效」会让一次手误立刻作用到所有对话上。

    `content_sha256` 不在可提交字段里：请求方自带摘要等于让调用方自己决定
    「校验通过」。
    """
    try:
        payload = await _strict_json_object(
            {"resource_id", "type", "content", "name", "description", "version"}
        )
        resource_id = payload.get("resource_id")
        resource_type = payload.get("type")
        content = payload.get("content")
        for label, value in (
            ("resource_id", resource_id),
            ("type", resource_type),
            ("content", content),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ResourceValidationError(f"{label} is required")
        for label in ("name", "description", "version"):
            value = payload.get(label)
            if value is not None and not isinstance(value, str):
                raise ResourceValidationError(f"{label} must be a string")

        version = payload.get("version") or "1.0.0"
        authored = await asyncio.to_thread(
            _service().author_document,
            resource_id=resource_id.strip(),
            resource_type=resource_type.strip(),
            content=content,
            name=payload.get("name"),
            description=payload.get("description"),
            version=version,
        )
        return jsonify(_resource_response(authored)), 201
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>/documents", methods=["PUT"])
# 写入一个新版本目录，与上传 ZIP 升级同一边界。
@require_creator("resources.manage")
async def author_resource_document_version(resource_id: str):
    """按新版本号写入改过的正文。

    改正文只能走版本递增：`content_sha256` 把清单与文件绑在一起，就地编辑的
    后果不是「改了没生效」，而是这个资源在下一次载入时直接失败。
    版本必须递增、旧版本保留（改错了要能回去）、装完保持停用等待确认。

    类型从已安装的注册表读而不是从请求体读——否则可以先上传一个 skill 的 ZIP、
    再用这条纯文本路径改它的正文，绕过打包与审阅。
    """
    try:
        payload = await _strict_json_object(
            {"content", "version", "name", "description"}
        )
        content = payload.get("content")
        version = payload.get("version")
        for label, value in (("content", content), ("version", version)):
            if not isinstance(value, str) or not value.strip():
                raise ResourceValidationError(f"{label} is required")
        for label in ("name", "description"):
            value = payload.get(label)
            if value is not None and not isinstance(value, str):
                raise ResourceValidationError(f"{label} must be a string")

        updated = await asyncio.to_thread(
            _service().author_document_version,
            resource_id,
            content=content,
            version=version.strip(),
            name=payload.get("name"),
            description=payload.get("description"),
        )
        return jsonify(_resource_response(updated))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/imports", methods=["POST"])
# 导入已有资源同样落盘。
@require_creator("resources.manage")
async def import_resource():
    return await _with_import_archive()


@resource_bp.route("/imports", methods=["GET"])
# 只读列举：不解包、不落盘，因此与其他只读接口同一边界。
@require_auth("resources.read")
async def list_importable_resources():
    """List archives already staged in ``resources/imports``.

    覆盖的是「用户手里没有可上传文件」的场景：运维用 scp 把一批包放进了服务器，
    或者包有几十 MB 走浏览器上传既慢又容易断。此前「导入已有」只接受浏览器
    上传，与「从ZIP安装」在机制上是同一件事——那让这个名字落不到实处。

    返回值里只有文件名，没有宿主机路径。
    """
    try:
        return jsonify({"imports": _service().discover_importable_archives()})
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/imports/install", methods=["POST"])
# 安装已在盘上的归档：解包落盘，与上传安装同一边界。
@require_creator("resources.manage")
async def install_importable_resource():
    """Install one archive discovered by ``GET /resources/imports``.

    请求体只接受一个 ``file_name``。**不接受路径**：允许路径就等于把一个
    只读列举接口变成任意文件安装接口。
    """
    try:
        data = await _strict_json_object({"file_name"})
        file_name = data.get("file_name")
        if not isinstance(file_name, str):
            return jsonify({"error": "file_name 必须是字符串"}), 400
        record = _service().import_discovered_archive(file_name)
        return jsonify(_resource_response(record)), 201
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>", methods=["GET"])
@require_auth("resources.read")
async def get_resource(resource_id: str):
    try:
        return jsonify(_resource_response(_service().get_resource(resource_id)))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>/content", methods=["GET"])
# 只读：不写盘、不执行任何东西。要求创建者身份会让「看一眼提示词写了什么」
# 变成一个需要提权的动作。
@require_auth("resources.read")
async def get_resource_content(resource_id: str):
    """Return one registered version's entry body together with its digest.

    需求 10 点名「提示词管理」。此前 prompt / skill / hook 只能走通用的
    安装 / 启用 / 停用 / 版本 / 备份生命周期，**没有任何地方能看到那份正文**——
    而 prompt 这个类型的全部内容就是正文：一个看不到正文的「提示词管理」
    回答不了它唯一要回答的问题。

    `ResourceLifecycleService.read_entry_metadata()` 早就存在且返回的正是这些，
    但零调用点——与 `UsageSource.ESTIMATED` 当初同一形态：有定义、有测试、
    主链路上没人用。

    **只读，且没有对应的写入路由。** 安装后的正文不能就地改：`content_sha256`
    把清单与文件绑在一起，`read_entry` 每次读取都重新校验摘要，就地编辑的后果
    不是「改了没生效」而是下一次载入直接失败。改正文的正确路径是装一个新版本
    （`POST /resources/<id>/versions`：版本号递增、自动备份、装完保持停用
    等待确认）。提供一个会破坏完整性契约的编辑框比不提供更糟。

    摘要一起返回，因为「用户看到的正文与运行时载入的是同一份」必须可自证，
    而不是靠信任。`version` 必须已注册——否则一个拼错的版本号会变成任意路径读取。
    """
    try:
        version = request.args.get("version") or None
        return jsonify(_service().read_entry_metadata(resource_id, version))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>", methods=["DELETE"])
# 卸载会删掉安装目录并从注册表里摘掉这个 ID：它写磁盘，也改变服务器能提供什么。
# 因此与依赖安装同一边界（创建者身份 + 显式确认），而不是与 `disable` 同一边界——
# 停用只是让资源不生效，是可逆的。
@require_creator("resources.manage")
async def delete_resource(resource_id: str):
    """卸载一个资源。删除前会自动备份当前版本。

    没有这条路由时，一个装错的 Skill / 不再用的 MCP 条目 / 写坏的 Prompt 只能被
    「停用」而永久留在列表里：停用不释放磁盘、不清注册表，也不让那个 ID 重新可用
    ——重装同名资源会撞「重复 ID」。运维唯一的办法是登服务器手改 `registry.json`。
    """
    try:
        payload = await _strict_json_object({"confirmed"})
        removed = await asyncio.to_thread(
            _service().remove,
            resource_id,
            confirmed=payload.get("confirmed") is True,
        )
        if removed.get("type") == "mcp" and g.container.has(MCPServerManager):
            # 删掉一个 mcp 资源之后必须刷新受管服务器，否则那个条目仍在运行——
            # 界面上资源已经不存在，服务器上进程还在。
            await g.container.resolve(MCPServerManager).refresh_managed_servers(
                connect=False
            )
        return jsonify(_resource_response(removed))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>/versions", methods=["POST"])
# 升级版本会写入一个新的版本目录。
@require_creator("resources.manage")
async def update_resource(resource_id: str):
    return await _with_uploaded_archive(
        lambda archive_path: _service().update_archive(
            archive_path,
            expected_resource_id=resource_id,
        ),
        success_status=200,
    )


@resource_bp.route("/<resource_id>/update", methods=["POST"])
# 远程升级会拉取上游内容并写入新版本目录。
@require_creator("resources.manage")
async def update_remote_resource(resource_id: str):
    try:
        updated = await asyncio.to_thread(_sources().update_skill, resource_id)
        return jsonify(_resource_response(updated))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>/enable", methods=["POST"])
# 启用是写操作：启用 mcp 资源会 refresh_managed_servers，在服务器上真正拉起
# 进程；启用 hook 会让它进入可执行链路。停用不需要同等约束（见下），
# 因为停用只会让扩展不再生效，不引入新的服务器副作用。
@require_creator("resources.manage")
async def enable_resource(resource_id: str):
    try:
        payload = await _json_object()
        resource = _service().enable(resource_id, confirmed=payload.get("confirmed") is True)
        if resource.get("type") == "mcp" and g.container.has(MCPServerManager):
            await g.container.resolve(MCPServerManager).refresh_managed_servers(
                connect=False
            )
        return jsonify(_resource_response(resource))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>/disable", methods=["POST"])
@require_auth("resources.manage")
async def disable_resource(resource_id: str):
    try:
        resource = _service().disable(resource_id)
        if resource.get("type") == "mcp" and g.container.has(MCPServerManager):
            await g.container.resolve(MCPServerManager).refresh_managed_servers(
                connect=False
            )
        return jsonify(_resource_response(resource))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>/workflow", methods=["POST"])
@require_auth("resources.manage")
async def bind_workflow(resource_id: str):
    return await _state_change(
        lambda payload: _service().bind_workflow(
            resource_id,
            payload.get("workflow_id"),
        )
    )


@resource_bp.route("/<resource_id>/runtime", methods=["PUT"])
# 覆盖决定 MCP 进程能读写哪些目录、带什么环境变量——那正是需求 10 说的
# 「通过插件修改服务器内容或执行文件操作」的范围本身，必须限创建者。
@require_creator("resources.manage")
async def set_resource_runtime(resource_id: str):
    """配置一条受管 MCP 资源在**这台机器**上怎么跑。

    此前受管 MCP 资源完全没有配置入口：`PUT /mcp/servers/<id>` 只在
    `config.mcp.servers` 里查找，而受管资源住在资源注册表里，因此那条路由对
    它们一律返回 404——尽管界面给每一行都渲染了「编辑」按钮。
    最明显的后果是 `mcp:filesystem`：它的描述要求「启用前必须在 args 末尾追加
    允许访问的目录」，而在产品里做不到这件事。

    只接受「这台机器怎么跑它」那几个键（`RUNTIME_OVERRIDE_KEYS`）。
    `command` / `args` / `type` / `url` / `id` 不在其中：那是 `content_sha256`
    保护的身份，从这个入口放开它们等于让「配一个可读目录」可以把 `npx`
    换成任意程序。未知字段返回 400 而不是静默忽略——静默忽略会让一个拼错的
    字段名看起来保存成功，而目录从未生效。

    写完刷新受管服务器（不自动连接）：不刷新的话进程还在用旧参数跑，
    而界面已经显示新配置了。
    """
    try:
        payload = await _strict_json_object(set(RUNTIME_OVERRIDE_KEYS))
        overrides = {
            key: payload[key] for key in RUNTIME_OVERRIDE_KEYS if key in payload
        }
        resource = _service().set_runtime_overrides(resource_id, **overrides)
        if g.container.has(MCPServerManager):
            await g.container.resolve(MCPServerManager).refresh_managed_servers(
                connect=False
            )
        return jsonify(_resource_response(resource))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>/restore", methods=["POST"])
# 回滚到历史版本会改变磁盘上生效的内容。
@require_creator("resources.manage")
async def restore_resource(resource_id: str):
    return await _state_change(
        lambda payload: _service().restore_version(
            resource_id,
            payload.get("version"),
            confirmed=payload.get("confirmed") is True,
        )
    )
