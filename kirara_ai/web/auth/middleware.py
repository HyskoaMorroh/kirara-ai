from collections.abc import Callable
from functools import wraps
from typing import Any

from quart import g, jsonify, request

from kirara_ai.web.auth.services import AuthService
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context


def _has_required_scopes(
    principal: RuntimePrincipal,
    required_scopes: tuple[str, ...],
) -> bool:
    return principal.has_scopes(required_scopes)


def _decorate(f: Callable[..., Any], required_scopes: tuple[str, ...]):
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            return jsonify({"error": "Invalid authorization header"}), 401

        token = parts[1]
        auth_service: AuthService = g.container.resolve(AuthService)
        principal = auth_service.get_runtime_principal(token)
        if principal is None:
            return jsonify({"error": "Invalid token"}), 401
        if not _has_required_scopes(principal, required_scopes):
            return jsonify({"error": "Insufficient permissions"}), 403

        g.auth_principal = principal
        with runtime_principal_context(principal):
            return await f(*args, **kwargs)

    return decorated_function


def require_auth(*required_scopes: str):
    """Require a Bearer token and optionally a complete set of scopes.

    Existing ``@require_auth`` routes remain valid. Query-string tokens are
    intentionally rejected because URLs are copied into logs and referrers.
    """
    if required_scopes and callable(required_scopes[0]) and len(required_scopes) == 1:
        return _decorate(required_scopes[0], ())

    normalized = tuple(scope.strip() for scope in required_scopes if scope.strip())

    def decorator(f: Callable[..., Any]):
        return _decorate(f, normalized)

    return decorator


def require_creator(*required_scopes: str):
    """Require the project creator, not merely a scoped token.

    需求 10 要求「只有该项目的创建者才能通过插件修改服务器内容或执行文件操作」。
    仅用 scope 判定不够：``AuthService`` 默认签发的 token 带 ``["*"]``
    （见 `services.py`），所以任何登录用户都能通过 ``resources.manage`` 检查。
    Agent 执行链路上有 ``principal_can_control_agent`` 把住这一层，
    但 HTTP 侧的依赖安装/任务重试端点只有 scope——两条路径的权限边界不一致，
    等于给了一条绕过创建者限制的入口。

    这里在 scope 之上再要求 ``is_creator``：非创建者得到 403 而不是 401，
    因为 token 本身是有效的，缺的是身份而不是凭据。
    """
    normalized = tuple(scope.strip() for scope in required_scopes if scope.strip())

    def decorator(f: Callable[..., Any]):
        # 身份检查包在业务函数外层、鉴权装饰器内层：这样 token 无效仍是 401、
        # scope 不足仍是 403，而 `g.auth_principal` 在检查时已经就绪。
        @wraps(f)
        async def creator_only(*args, **kwargs):
            principal = getattr(g, "auth_principal", None)
            if principal is None or not principal.is_creator:
                return jsonify(
                    {"error": "This operation is restricted to the project creator"}
                ), 403
            return await f(*args, **kwargs)

        return _decorate(creator_only, normalized)

    return decorator
