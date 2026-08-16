import pytest
from fastapi.testclient import TestClient

from kirara_ai.config.global_config import GlobalConfig, WebConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.dispatch import CombinedDispatchRule, DispatchRuleRegistry, RuleGroup, SimpleDispatchRule
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from tests.utils.auth_test_utils import auth_headers, setup_auth_service  # noqa


def make_rule(rule_id: str, priority: int, rule_groups=None) -> CombinedDispatchRule:
    return CombinedDispatchRule(
        rule_id=rule_id,
        name=rule_id,
        description="",
        workflow_id="chat:normal",
        priority=priority,
        enabled=True,
        rule_groups=rule_groups
        if rule_groups is not None
        else [
            RuleGroup(
                operator="or",
                rules=[SimpleDispatchRule(type="prefix", config={"prefix": f"/{rule_id}"})],
            )
        ],
        metadata={},
    )


@pytest.fixture
def container(tmp_path):
    container = DependencyContainer()
    config = GlobalConfig()
    config.web = WebConfig(
        secret_key="test-secret-key",
        password_file=str(tmp_path / "test_password.hash"),
    )
    container.register(GlobalConfig, config)
    setup_auth_service(container)

    workflow_registry = WorkflowRegistry(container)
    workflow_registry.register("chat", "normal", WorkflowBuilder("普通对话"))
    container.register(WorkflowRegistry, workflow_registry)
    dispatch_registry = DispatchRuleRegistry(container)
    dispatch_registry.rules_dir = str(tmp_path / "dispatch_rules")
    dispatch_registry.register(make_rule("zeta", 30))
    dispatch_registry.register(make_rule("alpha", 30))
    dispatch_registry.register(make_rule("system", 100))
    container.register(DispatchRuleRegistry, dispatch_registry)
    return container


@pytest.fixture
def dispatch_registry(container):
    return container.resolve(DispatchRuleRegistry)


@pytest.fixture
def app(container):
    return WebServer(container).app


@pytest.fixture
def test_client(app):
    return TestClient(app)


def test_rule_api_uses_the_same_stable_order_as_dispatcher(test_client, auth_headers):
    response = test_client.get("/backend-api/api/dispatch/rules", headers=auth_headers)

    assert response.status_code == 200
    assert [rule["rule_id"] for rule in response.json()["rules"]] == [
        "system",
        "alpha",
        "zeta",
    ]


def test_rule_list_carries_the_reachability_the_webui_renders(test_client, auth_headers):
    """WebUI 不再自行推导遮蔽关系，列表接口必须直接给出结论。"""
    response = test_client.get("/backend-api/api/dispatch/rules", headers=auth_headers)

    payload = response.json()
    reachability = payload["reachability"]
    # 与 rules 顺序逐条对齐，界面可以直接按下标渲染
    assert [item["rule_id"] for item in reachability] == [
        rule["rule_id"] for rule in payload["rules"]
    ]
    assert [item["order"] for item in reachability] == [1, 2, 3]
    assert all(item["unreachable"] is False for item in reachability)
    assert all(item["catch_all"] is False for item in reachability)


def test_reachability_endpoint_flags_a_draft_hidden_behind_a_catch_all_rule(
    test_client, auth_headers, dispatch_registry
):
    dispatch_registry.register(
        make_rule(
            "catch_all",
            50,
            rule_groups=[
                RuleGroup(operator="or", rules=[SimpleDispatchRule(type="fallback", config={})])
            ],
        )
    )
    draft = make_rule("draft", 10).model_dump()

    response = test_client.post(
        "/backend-api/api/dispatch/reachability",
        headers=auth_headers,
        json={"draft_rule": draft},
    )

    assert response.status_code == 200
    by_id = {item["rule_id"]: item for item in response.json()["reachability"]}
    assert by_id["draft"]["unreachable"] is True
    assert by_id["draft"]["shadowed_by_rule_id"] == "catch_all"
    assert by_id["catch_all"]["catch_all"] is True
    # 只做静态分析，不得写入任何规则
    assert dispatch_registry.get_rule("draft") is None


def test_reachability_endpoint_works_without_a_draft(test_client, auth_headers):
    response = test_client.post(
        "/backend-api/api/dispatch/reachability", headers=auth_headers, json={}
    )

    assert response.status_code == 200
    assert [item["rule_id"] for item in response.json()["reachability"]] == [
        "system",
        "alpha",
        "zeta",
    ]


@pytest.mark.parametrize("method,path", [("post", "/backend-api/api/dispatch/rules"), ("put", "/backend-api/api/dispatch/rules/alpha")])
def test_rule_api_rejects_a_non_fallback_rule_without_conditions(
    test_client, auth_headers, method, path
):
    rule = make_rule("alpha", 30, rule_groups=[]).model_dump()

    response = getattr(test_client, method)(path, headers=auth_headers, json=rule)

    assert response.status_code == 400
    assert "at least one condition" in response.json()["error"]


def test_rule_api_accepts_an_explicit_fallback_condition(test_client, auth_headers):
    fallback_rule = make_rule(
        "explicit_fallback",
        0,
        rule_groups=[
            RuleGroup(
                operator="or",
                rules=[SimpleDispatchRule(type="fallback", config={})],
            )
        ],
    ).model_dump()

    response = test_client.post(
        "/backend-api/api/dispatch/rules",
        headers=auth_headers,
        json=fallback_rule,
    )

    assert response.status_code == 200
    assert response.json()["rule"]["rule_id"] == "explicit_fallback"


def test_rule_preview_explains_real_dispatch_order_without_changing_rules(test_client, auth_headers):
    before = test_client.get("/backend-api/api/dispatch/rules", headers=auth_headers).json()["rules"]

    response = test_client.post(
        "/backend-api/api/dispatch/preview",
        headers=auth_headers,
        json={
            "content": "/alpha run this",
            "chat_type": "私聊",
            "sender_id": "preview-user",
            "mentioned": False,
        },
    )

    assert response.status_code == 200
    preview = response.json()
    assert preview["selected_rule_id"] == "alpha"
    assert preview["selected_workflow_id"] == "chat:normal"

    results = {item["rule_id"]: item for item in preview["rules"]}
    assert results["system"]["decision"] == "not_matched"
    assert results["alpha"]["decision"] == "selected"
    assert results["alpha"]["matched"] is True
    assert results["zeta"]["decision"] == "not_matched"

    after = test_client.get("/backend-api/api/dispatch/rules", headers=auth_headers).json()["rules"]
    assert after == before


def test_rule_preview_and_reachability_never_disagree_about_shadowing(
    test_client, auth_headers, dispatch_registry
):
    """试运行与静态可达性读同一份语义实现，两个接口的结论必须一致。"""
    dispatch_registry.register(
        make_rule(
            "catch_all",
            50,
            rule_groups=[
                RuleGroup(operator="or", rules=[SimpleDispatchRule(type="fallback", config={})])
            ],
        )
    )

    preview = test_client.post(
        "/backend-api/api/dispatch/preview",
        headers=auth_headers,
        json={
            "content": "/alpha run this",
            "chat_type": "私聊",
            "sender_id": "preview-user",
            "mentioned": False,
        },
    ).json()
    reachability = test_client.post(
        "/backend-api/api/dispatch/reachability", headers=auth_headers, json={}
    ).json()["reachability"]

    preview_by_id = {item["rule_id"]: item for item in preview["rules"]}
    reachability_by_id = {item["rule_id"]: item for item in reachability}
    assert preview_by_id.keys() == reachability_by_id.keys()
    for rule_id, item in reachability_by_id.items():
        assert preview_by_id[rule_id]["order"] == item["order"]
        assert preview_by_id[rule_id]["catch_all"] == item["catch_all"]
        assert preview_by_id[rule_id]["unreachable"] == item["unreachable"]
        assert preview_by_id[rule_id]["shadowed_by_rule_id"] == item["shadowed_by_rule_id"]

    # alpha 优先级 30 < 兜底规则的 50，因此静态上永远不可达；
    # 兜底规则先命中，本次消息里 alpha 的判定不再是 selected。
    assert reachability_by_id["alpha"]["unreachable"] is True
    assert preview["selected_rule_id"] == "catch_all"
