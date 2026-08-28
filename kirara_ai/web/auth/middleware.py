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
