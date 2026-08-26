import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


@pytest.fixture
def auth_api(tmp_path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(AuthService, MockAuthService(role="operator", scopes=["mcp.read"]))
    container.register(MCPServerManager, MCPServerManager(container))
    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.asyncio
async def test_malformed_authorization_is_a_stable_401(auth_api):
    response = await auth_api.get(
        "/api/mcp/servers", headers={"Authorization": "Bearer token extra"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_query_string_tokens_are_not_accepted(auth_api):
    response = await auth_api.get("/api/mcp/servers?auth_token=mock_token")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_scopes_separate_mcp_read_from_management(auth_api):
    allowed = await auth_api.get(
        "/api/mcp/servers", headers={"Authorization": "Bearer mock_token"}
    )
    denied = await auth_api.post(
        "/api/mcp/servers",
        headers={"Authorization": "Bearer mock_token"},
        json={"id": "blocked", "server": {"type": "http", "url": "https://example.invalid/mcp"}},
    )
    assert allowed.status_code == 200
    assert denied.status_code == 403
