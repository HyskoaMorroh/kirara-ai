from functools import wraps

from quart import g, jsonify, request

from kirara_ai.web.auth.services import AuthService


def require_auth(f):
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        # 如果 query string 中包含 token，则使用该 token
        token = request.args.get("auth_token")
        if not token:
            auth_header = request.headers.get("Authorization")
            if not auth_header:
                return jsonify({"error": "No authorization header"}), 401
            token_type, token = auth_header.split()
            if token_type.lower() != "bearer":
                return jsonify({"error": "Invalid token type"}), 401
        try:
            auth_service: AuthService = g.container.resolve(AuthService)
            if not auth_service.verify_token(token):
                return jsonify({"error": "Invalid token"}), 401

            return await f(*args, **kwargs)
        except Exception as e:
            raise e

    return decorated_function
