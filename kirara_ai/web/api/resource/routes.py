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
from kirara_ai.web.auth.middleware import require_auth, require_creator


resource_bp = Blueprint("resource", __name__)
logger = get_logger("Web.Resource")
MAX_RESOURCE_UPLOAD_SIZE = MAX_ARCHIVE_SIZE_BYTES


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


def _resource_response(resource: Any) -> Any:
    """Add transient runtime and VPS readiness fields to lifecycle objects."""

    if not isinstance(resource, dict) or not {"resource_id", "type"}.issubset(resource):
        return resource
    projected = deepcopy(resource)
    projected = _catalog().project_dependencies(projected)
    projected.update(_runtime_status(projected))
    projected.update(_binding_visibility(projected))
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
    try:
        return jsonify(
            _resource_list_response(_service().list_resources(request.args.get("type")))
        )
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
