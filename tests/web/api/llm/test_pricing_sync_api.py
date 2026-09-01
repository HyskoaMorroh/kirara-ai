"""定价同步的两个 HTTP 端点。

同步器与调度器都已经能跑（见 `tests/llm/test_pricing_upstream_sync.py` 与
`tests/scheduler/test_price_sync_schedule.py`），这里锁住 Web 层：权限与既有
定价路由同档、上游拉不到时不能报成成功、间隔天数落到配置里而不是只回显。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.pricing import PriceCatalog
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService

UPSTREAM = {
    "anthropic": {
        "models": {
            "claude-sonnet-5": {
                "cost": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75}
            }
        }
    }
}


def _make_api(tmp_path: Path, *, scopes: list[str]):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    container.register(GlobalConfig, config)
    container.register(
        AuthService, MockAuthService(scopes=scopes, subject="pricing-operator")
    )
    catalog = PriceCatalog.load_or_create(tmp_path / "pricing" / "catalog.json")
    container.register(PriceCatalog, catalog)
    container.register(ResourceLifecycleService, ResourceLifecycleService(tmp_path / "rt"))
    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), catalog, config


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


@pytest.mark.asyncio
async def test_sync_requires_the_pricing_manage_scope(tmp_path: Path):
    client, _, _ = _make_api(tmp_path, scopes=["llm.pricing.read"])

    response = await client.post("/api/llm/pricing/sync", headers=_headers())

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_sync_writes_upstream_rates_into_the_catalog(tmp_path: Path, monkeypatch):
    client, catalog, _ = _make_api(tmp_path, scopes=["llm.pricing.manage"])
    monkeypatch.setattr(
        "kirara_ai.llm.pricing_sync.fetch_upstream_catalog", lambda: UPSTREAM
    )

    response = await client.post("/api/llm/pricing/sync", headers=_headers())
    payload = await response.get_json()

    assert response.status_code == 200
    assert payload["imported"] == 1
    assert payload["error"] is None
    version = next(iter(catalog.values()))
    assert version.provider == "anthropic"
    assert version.input_per_million == Decimal("3")


@pytest.mark.asyncio
async def test_a_dead_upstream_is_reported_as_a_gateway_failure(tmp_path: Path, monkeypatch):
    """拉不到目录时不能返回 200——那会让界面显示「同步成功，更新 0 条」。"""
    client, _, _ = _make_api(tmp_path, scopes=["llm.pricing.manage"])

    def explode() -> dict:
        raise TimeoutError("upstream unreachable")

    monkeypatch.setattr("kirara_ai.llm.pricing_sync.fetch_upstream_catalog", explode)

    response = await client.post("/api/llm/pricing/sync", headers=_headers())
    payload = await response.get_json()

    assert response.status_code == 502
    assert "unreachable" in payload["error"]


@pytest.mark.asyncio
async def test_the_interval_lands_in_the_config_not_just_the_response(tmp_path: Path):
    client, _, config = _make_api(tmp_path, scopes=["llm.pricing.manage"])

    response = await client.put(
        "/api/llm/pricing/sync-schedule", headers=_headers(), json={"interval_days": 3}
    )

    assert response.status_code == 200
    assert config.llms.price_sync_interval_days == 3


@pytest.mark.asyncio
async def test_zero_turns_the_schedule_off_and_is_not_treated_as_missing(tmp_path: Path):
    client, _, config = _make_api(tmp_path, scopes=["llm.pricing.manage"])

    response = await client.put(
        "/api/llm/pricing/sync-schedule", headers=_headers(), json={"interval_days": 0}
    )

    assert response.status_code == 200
    assert config.llms.price_sync_interval_days == 0


@pytest.mark.asyncio
async def test_a_negative_interval_is_rejected(tmp_path: Path):
    client, _, config = _make_api(tmp_path, scopes=["llm.pricing.manage"])

    response = await client.put(
        "/api/llm/pricing/sync-schedule", headers=_headers(), json={"interval_days": -1}
    )

    assert response.status_code == 400
    assert config.llms.price_sync_interval_days == 7
