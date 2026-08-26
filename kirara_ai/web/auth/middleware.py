from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any

from quart import g, jsonify, request

from kirara_ai.web.auth.services import AuthService


def _has_required_scopes(claims: Mapping[str, Any], required_scopes: tuple[str, ...]) -> bool:
    if not required_scopes:
        return True
    role = str(claims.get("role", "")).lower()
    raw_scopes = claims.get("scopes", [])
    scopes = {str(scope) for scope in raw_scopes if isinstance(scope, str)}
    return role == "admin" or "*" in scopes or set(required_scopes).issubset(scopes)


def _decorate(f: Callable[..., Any], required_scopes: tuple[str, ...]):
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            return jsonify({"error": "Invalid authorization header"}), 401

        token = parts[1]
        auth_service: AuthService = g.container.resolve(AuthService)
        claims = auth_service.get_token_claims(token)
        if not claims:
            return jsonify({"error": "Invalid token"}), 401
        if not _has_required_scopes(claims, required_scopes):
            return jsonify({"error": "Insufficient permissions"}), 403

        g.auth_principal = claims
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
