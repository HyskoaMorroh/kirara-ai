from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Awaitable, Callable

from quart import Blueprint, g, jsonify, request

from kirara_ai.logger import get_logger
from kirara_ai.plugin_manager.resource_lifecycle import (
    MAX_ARCHIVE_SIZE_BYTES,
    ResourceLifecycleService,
    ResourceStateError,
    ResourceValidationError,
)
from kirara_ai.plugin_manager.resource_sources import ResourceSourceError, ResourceSourceService
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogError, ResourceCatalogService
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.web.auth.middleware import require_auth


resource_bp = Blueprint("resource", __name__)
logger = get_logger("Web.Resource")
MAX_RESOURCE_UPLOAD_SIZE = MAX_ARCHIVE_SIZE_BYTES


def _service() -> ResourceLifecycleService:
    return g.container.resolve(ResourceLifecycleService)


def _sources() -> ResourceSourceService:
    return g.container.resolve(ResourceSourceService)


def _catalog() -> ResourceCatalogService:
    return g.container.resolve(ResourceCatalogService)


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


def _resource_response(resource: Any) -> Any:
    """Add runtime fields only to lifecycle resource objects."""

    if not isinstance(resource, dict) or not {"resource_id", "type"}.issubset(resource):
        return resource
    projected = deepcopy(resource)
    projected.update(_runtime_status(projected))
    return projected


def _resource_list_response(resources: Any) -> Any:
    if not isinstance(resources, list):
        return resources
    return [_resource_response(resource) for resource in resources]


def _lifecycle_error(error: Exception):
    if isinstance(error, (ResourceValidationError, ResourceSourceError, ResourceCatalogError)):
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


@resource_bp.route("/repositories", methods=["GET"])
@require_auth
async def list_repositories():
    try:
        return jsonify(_sources().list_repositories())
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/repositories", methods=["POST"])
@require_auth
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
@require_auth
async def set_repository_enabled(owner: str, name: str, branch: str):
    try:
        payload = await _json_object()
        if not isinstance(payload.get("enabled"), bool):
            raise ResourceSourceError("repository enabled must be boolean")
        return jsonify(_sources().set_repository_enabled(owner, name, branch, payload["enabled"]))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/repositories/<owner>/<name>/<branch>/discover", methods=["GET"])
@require_auth
async def discover_repository(owner: str, name: str, branch: str):
    try:
        return jsonify(_sources().discover_repository(owner, name, branch))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/skills-sh/search", methods=["GET"])
@require_auth
async def search_skills_sh():
    try:
        return jsonify(
            _sources().search_skills(
                request.args.get("q", ""),
                limit=int(request.args.get("limit", 20)),
                offset=int(request.args.get("offset", 0)),
            )
        )
    except (TypeError, ValueError):
        return _error("search pagination values must be integers", 400)
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/catalog/search", methods=["GET"])
@require_auth
async def search_catalog():
    try:
        return jsonify(
            _catalog().search(
                request.args.get("type"),
                request.args.get("q", ""),
                limit=int(request.args.get("limit", 20)),
                offset=int(request.args.get("offset", 0)),
            )
        )
    except (TypeError, ValueError):
        return _error("catalog pagination values must be integers", 400)
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/catalog/<path:catalog_id>", methods=["GET"])
@require_auth
async def get_catalog_item(catalog_id: str):
    try:
        return jsonify(_catalog().get(catalog_id))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/catalog/install", methods=["POST"])
@require_auth
async def install_catalog_item():
    try:
        payload = await _json_object()
        catalog_id = payload.get("catalog_id")
        if not isinstance(catalog_id, str) or not catalog_id.strip():
            raise ResourceCatalogError("catalog_id is required")
        return jsonify(
            _resource_response(
                _catalog().install(catalog_id.strip(), branch=payload.get("branch", "main"))
            )
        ), 201
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/remote-install", methods=["POST"])
@require_auth
async def install_remote_skill():
    try:
        payload = await _json_object()
        return jsonify(_resource_response(_sources().install_skill(**payload))), 201
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/updates", methods=["GET"])
@require_auth
async def check_resource_updates():
    try:
        return jsonify(_sources().check_updates(request.args.get("resource_id")))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/backups", methods=["GET"])
@require_auth
async def list_backups():
    try:
        return jsonify(_service().list_backups(request.args.get("resource_id")))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/backups/<backup_id>/restore", methods=["POST"])
@require_auth
async def restore_backup(backup_id: str):
    return await _state_change(
        lambda payload: _service().restore_backup(
            backup_id, confirmed=payload.get("confirmed") is True
        )
    )


@resource_bp.route("/backups/<backup_id>", methods=["DELETE"])
@require_auth
async def delete_backup(backup_id: str):
    return await _state_change(
        lambda payload: _service().delete_backup(
            backup_id, confirmed=payload.get("confirmed") is True
        )
    )


# Static routes are declared before the resource ID route to keep /audit explicit.
@resource_bp.route("/audit", methods=["GET"])
@require_auth
async def list_audit():
    try:
        offset = int(request.args.get("offset", 0))
        limit = int(request.args.get("limit", 50))
        resource_id = request.args.get("resource_id")
        return jsonify(
            _service().list_audit(
                offset=offset,
                limit=limit,
                resource_id=resource_id,
            )
        )
    except (TypeError, ValueError):
        return _error("audit pagination values must be integers", 400)
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/storage", methods=["GET"])
@require_auth
async def get_storage_status():
    try:
        return jsonify(_service().get_storage_status())
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("", methods=["GET"])
@require_auth
async def list_resources():
    try:
        return jsonify(
            _resource_list_response(_service().list_resources(request.args.get("type")))
        )
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("", methods=["POST"])
@require_auth
async def install_resource():
    return await _with_uploaded_archive(
        _service().install_archive,
        success_status=201,
    )


@resource_bp.route("/imports", methods=["POST"])
@require_auth
async def import_resource():
    return await _with_import_archive()


@resource_bp.route("/<resource_id>", methods=["GET"])
@require_auth
async def get_resource(resource_id: str):
    try:
        return jsonify(_resource_response(_service().get_resource(resource_id)))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>/versions", methods=["POST"])
@require_auth
async def update_resource(resource_id: str):
    return await _with_uploaded_archive(
        lambda archive_path: _service().update_archive(
            archive_path,
            expected_resource_id=resource_id,
        ),
        success_status=200,
    )


@resource_bp.route("/<resource_id>/update", methods=["POST"])
@require_auth
async def update_remote_resource(resource_id: str):
    try:
        return jsonify(_resource_response(_sources().update_skill(resource_id)))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>/enable", methods=["POST"])
@require_auth
async def enable_resource(resource_id: str):
    try:
        payload = await _json_object()
        resource = _service().enable(resource_id, confirmed=payload.get("confirmed") is True)
        if resource.get("type") == "mcp" and g.container.has(MCPServerManager):
            await g.container.resolve(MCPServerManager).refresh_managed_servers()
        return jsonify(_resource_response(resource))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>/disable", methods=["POST"])
@require_auth
async def disable_resource(resource_id: str):
    try:
        resource = _service().disable(resource_id)
        if resource.get("type") == "mcp" and g.container.has(MCPServerManager):
            await g.container.resolve(MCPServerManager).refresh_managed_servers()
        return jsonify(_resource_response(resource))
    except Exception as error:
        return _lifecycle_error(error)


@resource_bp.route("/<resource_id>/workflow", methods=["POST"])
@require_auth
async def bind_workflow(resource_id: str):
    return await _state_change(
        lambda payload: _service().bind_workflow(
            resource_id,
            payload.get("workflow_id"),
        )
    )


@resource_bp.route("/<resource_id>/restore", methods=["POST"])
@require_auth
async def restore_resource(resource_id: str):
    return await _state_change(
        lambda payload: _service().restore_version(
            resource_id,
            payload.get("version"),
            confirmed=payload.get("confirmed") is True,
        )
    )
