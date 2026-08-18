from pathlib import Path

import pytest
from ruamel.yaml import YAML
from fastapi.testclient import TestClient

from kirara_ai.config.global_config import GlobalConfig, WebConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.block import Block, BlockRegistry
from kirara_ai.workflow.core.block.input_output import Input, Output
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from tests.utils.auth_test_utils import auth_headers, setup_auth_service  # noqa

# ==================== 常量区 ====================
TEST_PASSWORD = "test-password"
TEST_SECRET_KEY = "test-secret-key"
TEST_GROUP_ID = "test-group"
TEST_WORKFLOW_ID = "test-workflow"
TEST_WORKFLOW_ID_NEW = "test-workflow-new"
TEST_WORKFLOW_NAME = "Test Workflow"
TEST_WORKFLOW_NAME_NEW = "Test Workflow New"
TEST_WORKFLOW_DESC = "A test workflow"


# ==================== 测试用Block ====================
class MessageBlock(Block):
    name = "message_block"
    inputs = {}
    outputs = {"output": Output("output", "输出", str, "Output message")}
    container: DependencyContainer

    def __init__(self, text: str = ""):
        self.config = {"text": text}
        self.position = {"x": 0, "y": 0}

    def execute(self) -> dict:
        return {"output": self.config["text"]}


class LLMBlock(Block):
    name = "llm_block"
    inputs = {"input": Input("input", "输入", str, "Input message")}
    outputs = {"output": Output("output", "输出", str, "Output message")}
    container: DependencyContainer

    def __init__(self, prompt: str = ""):
        self.config = {"prompt": prompt}

        self.position = {"x": 200, "y": 0}

    def execute(self, input: str) -> dict:
        return {"output": f"Response to: {input}"}


# ==================== Fixtures ====================
@pytest.fixture
def app(tmp_path, monkeypatch):
    """创建测试应用实例"""
    # API tests must not read or write the developer's real `data/workflows`
    # directory.  In particular, a prior test run may leave an ignored test
    # YAML behind, which would otherwise make a fresh in-memory registry look
    # like a collision.
    monkeypatch.setattr(WorkflowRegistry, "WORKFLOWS_DIR", str(tmp_path / "workflows"))
    container = DependencyContainer()

    # 配置
    config = GlobalConfig()
    config.web = WebConfig(
        secret_key=TEST_SECRET_KEY, password_file="test_password.hash"
    )
    container.register(GlobalConfig, config)

    # 设置认证服务
    setup_auth_service(container)

    # 创建并注册 BlockRegistry
    block_registry = BlockRegistry()
    block_registry.register("message", "test", MessageBlock)
    block_registry.register("llm", "test", LLMBlock)
    container.register(BlockRegistry, block_registry)

    # 创建工作流
    builder = (
        WorkflowBuilder(TEST_WORKFLOW_NAME)
        .use(MessageBlock, text="Hello")
        .chain(LLMBlock, prompt="How are you?")
    )

    # 创建并注册 WorkflowRegistry
    registry = WorkflowRegistry(container)
    registry.register(TEST_GROUP_ID, TEST_WORKFLOW_ID, builder)
    container.register(WorkflowRegistry, registry)

    web_server = WebServer(container)
    container.register(WebServer, web_server)
    return web_server.app


@pytest.fixture
def test_client(app):
    """创建测试客户端"""
    return TestClient(app)


# ==================== 测试用例 ====================
class TestWorkflow:
    @pytest.mark.asyncio
    async def test_validate_workflow_reports_structural_errors_without_persisting(
        self, test_client, auth_headers
    ):
        """预检必须只诊断草稿，不创建 YAML 或改写当前注册表。"""
        before = test_client.get("/backend-api/api/workflow", headers=auth_headers).json()
        draft = {
            "workflow_id": "draft",
            "group_id": TEST_GROUP_ID,
            "name": "Draft validation",
            "description": "",
            "blocks": [
                {"type_name": "test:message", "name": "source", "config": {}},
                {"type_name": "test:llm", "name": "target", "config": {}},
                {"type_name": "missing:plugin", "name": "unknown", "config": {}},
            ],
            "wires": [
                {
                    "source_block": "source",
                    "source_output": "missing_output",
                    "target_block": "target",
                    "target_input": "input",
                }
            ],
        }

        response = test_client.post(
            "/backend-api/api/workflow/validate", headers=auth_headers, json=draft
        )

        assert response.status_code == 200
        issues = response.json()
        assert {issue["code"] for issue in issues["errors"]} >= {
            "unknown_block_type",
            "unknown_output_port",
            "missing_required_input",
        }
        assert test_client.get("/backend-api/api/workflow", headers=auth_headers).json() == before

    @pytest.mark.asyncio
    async def test_list_workflows(self, test_client, auth_headers):
        """测试获取工作流列表"""
        response = test_client.get(
            "/backend-api/api/workflow", headers=auth_headers
        )
        data = response.json()
        assert "error" not in data
        assert "workflows" in data
        workflows = data["workflows"]
        assert len(workflows) == 1
        workflow = workflows[0]
        assert workflow["workflow_id"] == TEST_WORKFLOW_ID
        assert workflow["group_id"] == TEST_GROUP_ID
        assert workflow["name"] == TEST_WORKFLOW_NAME

    @pytest.mark.asyncio
    async def test_list_workflows_loads_catalog_once_and_keeps_metadata_optional(
        self, test_client, auth_headers, monkeypatch
    ):
        from kirara_ai.web.api.workflow import routes

        calls = 0
        original = routes.load_preset_catalog

        def counting_loader():
            nonlocal calls
            calls += 1
            return original()

        monkeypatch.setattr(routes, "load_preset_catalog", counting_loader)
        response = test_client.get("/backend-api/api/workflow", headers=auth_headers)
        assert response.status_code == 200
        assert calls == 1
        assert response.json()["workflows"][0]["metadata"] is None

    @pytest.mark.asyncio
    async def test_get_workflow(self, test_client, auth_headers):
        """测试获取单个工作流"""
        response = test_client.get(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID}",
            headers=auth_headers,
        )
        data = response.json()
        assert "error" not in data
        assert "workflow" in data
        workflow = data["workflow"]
        assert workflow["workflow_id"] == TEST_WORKFLOW_ID
        assert workflow["group_id"] == TEST_GROUP_ID
        assert workflow["name"] == TEST_WORKFLOW_NAME
        assert len(workflow["wires"]) == 1
        assert workflow["blocks"][0]["position"] is None

    @pytest.mark.asyncio
    async def test_workflow_api_preserves_parallel_markers_on_round_trip(
        self, test_client, auth_headers
    ):
        """Canvas saves must not silently turn parallel nodes into serial ones."""
        workflow_data = {
            "workflow_id": TEST_WORKFLOW_ID_NEW,
            "group_id": TEST_GROUP_ID,
            "name": "Parallel Workflow",
            "description": TEST_WORKFLOW_DESC,
            "blocks": [
                {
                    "type_name": "test:message",
                    "name": "source",
                    "config": {"text": "Hello"},
                    "parallel": True,
                },
                {
                    "type_name": "test:message",
                    "name": "serial",
                    "config": {"text": "Later"},
                },
            ],
            "wires": [],
        }

        response = test_client.post(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID_NEW}",
            headers=auth_headers,
            json=workflow_data,
        )
        assert response.status_code == 200

        stored = test_client.get(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID_NEW}",
            headers=auth_headers,
        )
        assert stored.status_code == 200
        assert [block["parallel"] for block in stored.json()["workflow"]["blocks"]] == [
            True,
            False,
        ]

        workflow_path = (
            Path(WorkflowRegistry.WORKFLOWS_DIR)
            / TEST_GROUP_ID
            / f"{TEST_WORKFLOW_ID_NEW}.yaml"
        )
        yaml = YAML(typ="safe")
        persisted = yaml.load(workflow_path.read_text(encoding="utf-8"))
        assert persisted["blocks"][0]["parallel"] is True
        assert "parallel" not in persisted["blocks"][1]

        update = {**workflow_data, "name": "Parallel Workflow Updated"}
        update_response = test_client.put(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID_NEW}",
            headers=auth_headers,
            json=update,
        )
        assert update_response.status_code == 200

        updated = test_client.get(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID_NEW}",
            headers=auth_headers,
        )
        assert updated.status_code == 200
        assert [block["parallel"] for block in updated.json()["workflow"]["blocks"]] == [
            True,
            False,
        ]

        persisted_after_update = yaml.load(workflow_path.read_text(encoding="utf-8"))
        assert persisted_after_update["blocks"][0]["parallel"] is True

    @pytest.mark.asyncio
    async def test_create_workflow(self, test_client, auth_headers):
        """测试创建工作流"""
        workflow_data = {
            "workflow_id": TEST_WORKFLOW_ID_NEW,
            "group_id": TEST_GROUP_ID,
            "name": TEST_WORKFLOW_NAME,
            "description": TEST_WORKFLOW_DESC,
            "metadata": {"category": "chat", "tags": ["test"]},
            "blocks": [
                {
                    "block_id": "node1",
                    "type_name": "test:message",
                    "name": "Message Node",
                    "config": {"text": "Hello"},
                    "position": {"x": 0, "y": 0},
                }
            ],
            "wires": [],
        }

        response = test_client.post(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID_NEW}",
            headers=auth_headers,
            json=workflow_data,
        )

        data = response.json()
        assert "error" not in data
        assert data["workflow_id"] == TEST_WORKFLOW_ID_NEW
        assert data["group_id"] == TEST_GROUP_ID
        assert data["name"] == TEST_WORKFLOW_NAME
        assert len(data["blocks"]) == 1
        assert data["metadata"] == {"category": "chat", "tags": ["test"]}

        stored = test_client.get(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID_NEW}",
            headers=auth_headers,
        ).json()["workflow"]
        assert stored["metadata"] == {"category": "chat", "tags": ["test"]}

    @pytest.mark.asyncio
    async def test_update_workflow(self, test_client, auth_headers):
        """测试更新工作流"""
        workflow_data = {
            "workflow_id": TEST_WORKFLOW_ID,
            "group_id": TEST_GROUP_ID,
            "name": "Updated Workflow",
            "description": "Updated workflow description",
            "blocks": [
                {
                    "block_id": "node1",
                    "type_name": "test:message",
                    "name": "Message Node",
                    "config": {"text": "Updated text"},
                    "position": {"x": 0, "y": 0},
                }
            ],
            "wires": [],
        }

        response = test_client.put(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID}",
            headers=auth_headers,
            json=workflow_data,
        )

        data = response.json()
        assert "error" not in data
        assert data["workflow_id"] == TEST_WORKFLOW_ID
        assert data["group_id"] == TEST_GROUP_ID
        assert data["name"] == "Updated Workflow"
        assert data["description"] == "Updated workflow description"
        assert len(data["blocks"]) == 1
        assert data["blocks"][0]["config"]["text"] == "Updated text"

    @pytest.mark.asyncio
    async def test_rename_workflow_rejects_an_existing_target(self, test_client, auth_headers):
        """Changing an ID must not overwrite an existing workflow."""
        target_data = {
            "workflow_id": TEST_WORKFLOW_ID_NEW,
            "group_id": TEST_GROUP_ID,
            "name": "Collision target",
            "description": TEST_WORKFLOW_DESC,
            "blocks": [
                {
                    "block_id": "node1",
                    "type_name": "test:message",
                    "name": "Message Node",
                    "config": {"text": "Target text"},
                    "position": {"x": 0, "y": 0},
                }
            ],
            "wires": [],
        }
        create_response = test_client.post(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID_NEW}",
            headers=auth_headers,
            json=target_data,
        )
        assert create_response.status_code == 200

        source_as_target = {**target_data, "name": "Should not overwrite target"}
        response = test_client.put(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID}",
            headers=auth_headers,
            json=source_as_target,
        )

        assert response.status_code == 409
        assert test_client.get(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID}",
            headers=auth_headers,
        ).status_code == 200
        target_response = test_client.get(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID_NEW}",
            headers=auth_headers,
        )
        assert target_response.status_code == 200
        assert target_response.json()["workflow"]["name"] == "Collision target"

    @pytest.mark.asyncio
    async def test_rename_workflow_rejects_a_target_file_outside_registry(
        self, test_client, auth_headers, monkeypatch
    ):
        """A stale target YAML must not be overwritten just because it was not loaded."""
        from kirara_ai.web.api.workflow import routes as workflow_routes

        original_exists = workflow_routes.os.path.exists

        def target_file_exists(file_path):
            return file_path.endswith(f"{TEST_WORKFLOW_ID_NEW}.yaml") or original_exists(file_path)

        monkeypatch.setattr(workflow_routes.os.path, "exists", target_file_exists)
        workflow_data = {
            "workflow_id": TEST_WORKFLOW_ID_NEW,
            "group_id": TEST_GROUP_ID,
            "name": "Must not overwrite an unregistered file",
            "description": TEST_WORKFLOW_DESC,
            "blocks": [
                {
                    "block_id": "node1",
                    "type_name": "test:message",
                    "name": "Message Node",
                    "config": {"text": "Hello"},
                    "position": {"x": 0, "y": 0},
                }
            ],
            "wires": [],
        }

        response = test_client.put(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID}",
            headers=auth_headers,
            json=workflow_data,
        )

        assert response.status_code == 409
        assert test_client.get(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID}",
            headers=auth_headers,
        ).status_code == 200

    @pytest.mark.asyncio
    async def test_delete_workflow(self, test_client, auth_headers):
        """测试删除工作流"""
        response = test_client.delete(
            f"/backend-api/api/workflow/{TEST_GROUP_ID}/{TEST_WORKFLOW_ID}",
            headers=auth_headers,
        )

        data = response.json()
        assert "error" not in data
        assert "message" in data
        assert data["message"] == "Workflow deleted successfully"
