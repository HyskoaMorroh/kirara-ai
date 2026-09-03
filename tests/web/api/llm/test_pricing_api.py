from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.pricing import PriceCatalog, PriceVersion
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


def _version(
    version_id: str = "provider-a:model-a:2026-08-27",
    *,
    effective_from: str = "2026-08-27T00:00:00Z",
    input_rate: str = "1.25",
    display_name: str | None = None,
) -> dict[str, str]:
    payload = {
        "version_id": version_id,
        "provider": "provider-a",
        "model": "model-a",
        "effective_from": effective_from,
        "currency": "USD",
        "input_per_million": input_rate,
        "output_per_million": "5.5",
        "cache_read_per_million": "0.125",
        "cache_write_per_million": "1.75",
    }
    # 只在显式给出时带上这个键：既有用例断言的正是「不提交它也能建」，
    # 无条件塞一个 `None` 会把那条断言变成测另一件事。
    if display_name is not None:
        payload["display_name"] = display_name
    return payload


def _catalog_version(version_id: str, hour: int) -> PriceVersion:
    return PriceVersion.model_validate(
        _version(
            version_id,
            effective_from=datetime(2026, 8, 27, hour, tzinfo=timezone.utc).isoformat(),
        )
    )


def _make_api(tmp_path: Path, *, scopes: list[str]):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(
        AuthService,
        MockAuthService(scopes=scopes, subject="pricing-operator"),
    )
    catalog = PriceCatalog.load_or_create(tmp_path / "pricing" / "catalog.json")
    lifecycle = ResourceLifecycleService(tmp_path / "runtime")
    container.register(PriceCatalog, catalog)
    container.register(ResourceLifecycleService, lifecycle)
    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), catalog, lifecycle


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


@pytest.mark.asyncio
async def test_pricing_read_routes_require_authentication_and_read_scope(tmp_path: Path):
    client, _, _ = _make_api(tmp_path, scopes=["llm.pricing.manage"])

    unauthenticated = await client.get("/api/llm/pricing")
    forbidden = await client.get("/api/llm/pricing", headers=_headers())

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403

    read_client, _, _ = _make_api(tmp_path / "read", scopes=["llm.pricing.read"])
    allowed = await read_client.get("/api/llm/pricing", headers=_headers())
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_pricing_manage_routes_require_manage_scope(tmp_path: Path):
    client, _, _ = _make_api(tmp_path, scopes=["llm.pricing.read"])

    response = await client.post(
        "/api/llm/pricing",
        headers=_headers(),
        json={"expected_revision": 0, "version": _version()},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_pricing_crud_serializes_decimal_rates_as_strings_and_tracks_revision(
    tmp_path: Path,
):
    client, catalog, _ = _make_api(tmp_path, scopes=["*"])

    created = await client.post(
        "/api/llm/pricing",
        headers=_headers(),
        json={"expected_revision": 0, "version": _version()},
    )
    assert created.status_code == 201
    created_payload = await created.get_json()
    assert created_payload["data"]["revision"] == 1
    assert created_payload["data"]["version"]["input_per_million"] == "1.25"

    listed = await client.get("/api/llm/pricing", headers=_headers())
    listed_payload = await listed.get_json()
    assert listed_payload["data"] == {
        "revision": 1,
        "versions": [created_payload["data"]["version"]],
        "backup_generations": [1],
    }

    version_id = _version()["version_id"]
    detail = await client.get(f"/api/llm/pricing/{version_id}", headers=_headers())
    assert detail.status_code == 200
    assert (await detail.get_json())["data"]["version"]["output_per_million"] == "5.5"

    updated_version = _version(input_rate="2.75")
    updated = await client.put(
        f"/api/llm/pricing/{version_id}",
        headers=_headers(),
        json={"expected_revision": 1, "version": updated_version},
    )
    assert updated.status_code == 200
    assert (await updated.get_json())["data"]["version"]["input_per_million"] == "2.75"

    confirmation_required = await client.delete(
        f"/api/llm/pricing/{version_id}",
        headers=_headers(),
        json={"expected_revision": 2},
    )
    assert confirmation_required.status_code == 400
    assert catalog.revision == 2

    deleted = await client.delete(
        f"/api/llm/pricing/{version_id}",
        headers=_headers(),
        json={"expected_revision": 2, "confirmed": True},
    )
    assert deleted.status_code == 200
    assert (await deleted.get_json())["data"] == {
        "revision": 3,
        "version_id": version_id,
    }
    assert catalog.values() == ()


@pytest.mark.asyncio
async def test_pricing_rejects_invalid_payloads_and_identifier_mismatch(tmp_path: Path):
    client, catalog, _ = _make_api(tmp_path, scopes=["*"])

    invalid = await client.post(
        "/api/llm/pricing",
        headers=_headers(),
        json={"expected_revision": 0, "version": _version(effective_from="not-a-date")},
    )
    mismatch = await client.put(
        "/api/llm/pricing/route-id",
        headers=_headers(),
        json={"expected_revision": 0, "version": _version("body-id")},
    )

    assert invalid.status_code == 400
    assert mismatch.status_code == 400
    assert catalog.values() == ()


@pytest.mark.asyncio
async def test_pricing_returns_not_found_for_unknown_version(tmp_path: Path):
    client, _, _ = _make_api(tmp_path, scopes=["*"])

    detail = await client.get("/api/llm/pricing/missing", headers=_headers())
    update = await client.put(
        "/api/llm/pricing/missing",
        headers=_headers(),
        json={"expected_revision": 0, "version": _version("missing")},
    )
    delete = await client.delete(
        "/api/llm/pricing/missing",
        headers=_headers(),
        json={"expected_revision": 0, "confirmed": True},
    )

    assert detail.status_code == 404
    assert update.status_code == 404
    assert delete.status_code == 404


@pytest.mark.asyncio
async def test_pricing_conflict_reports_expected_and_current_revision(tmp_path: Path):
    client, catalog, lifecycle = _make_api(tmp_path, scopes=["*"])
    other_process = PriceCatalog.load(catalog.path)
    other_process.add(_catalog_version("external", 1), expected_revision=0)

    response = await client.post(
        "/api/llm/pricing",
        headers=_headers(),
        json={"expected_revision": 0, "version": _version("stale")},
    )

    assert response.status_code == 409
    assert await response.get_json() == {
        "error": "Price catalog revision conflict",
        "code": "revision_conflict",
        "expected_revision": 0,
        "current_revision": 1,
    }
    assert not lifecycle.audit_path.exists()


@pytest.mark.asyncio
async def test_pricing_export_and_structured_import_are_validated_and_atomic(tmp_path: Path):
    client, catalog, _ = _make_api(tmp_path, scopes=["*"])
    catalog.add(_catalog_version("existing", 1), expected_revision=0)

    exported = await client.get("/api/llm/pricing/export", headers=_headers())
    assert exported.status_code == 200
    exported_payload = await exported.get_json()
    assert exported_payload["schema"] == "kirara-ai.price-catalog"
    assert exported_payload["versions"][0]["input_per_million"] == "1.25"
    assert "attachment" in exported.headers["Content-Disposition"]

    import_document = PriceCatalog([_catalog_version("imported", 2)]).export_document()
    imported = await client.post(
        "/api/llm/pricing/import",
        headers=_headers(),
        json={"expected_revision": 1, "catalog": import_document},
    )
    assert imported.status_code == 200
    assert (await imported.get_json())["data"] == {
        "revision": 2,
        "imported_count": 1,
    }
    assert [item.version_id for item in catalog.values()] == ["existing", "imported"]

    tampered = dict(import_document)
    tampered["integrity"] = "0" * 64
    rejected = await client.post(
        "/api/llm/pricing/import",
        headers=_headers(),
        json={"expected_revision": 2, "catalog": tampered},
    )
    assert rejected.status_code == 400
    assert [item.version_id for item in catalog.values()] == ["existing", "imported"]


@pytest.mark.asyncio
async def test_pricing_restore_requires_confirmation_and_supports_backup_generation(
    tmp_path: Path,
):
    client, catalog, _ = _make_api(tmp_path, scopes=["*"])
    catalog.add(_catalog_version("v1", 1), expected_revision=0)
    catalog.add(_catalog_version("v2", 2), expected_revision=1)

    unconfirmed = await client.post(
        "/api/llm/pricing/restore",
        headers=_headers(),
        json={"expected_revision": 2, "generation": 1},
    )
    assert unconfirmed.status_code == 400

    restored = await client.post(
        "/api/llm/pricing/restore",
        headers=_headers(),
        json={"expected_revision": 2, "generation": 1, "confirmed": True},
    )
    assert restored.status_code == 200
    assert (await restored.get_json())["data"] == {
        "revision": 3,
        "restored_generation": 1,
        "version_count": 1,
    }
    assert [item.version_id for item in catalog.values()] == ["v1"]


@pytest.mark.asyncio
async def test_pricing_audits_only_successful_management_with_subject_digest(tmp_path: Path):
    client, _, lifecycle = _make_api(tmp_path, scopes=["*"])

    rejected = await client.post(
        "/api/llm/pricing",
        headers=_headers(),
        json={"expected_revision": 0, "version": _version(effective_from="invalid")},
    )
    assert rejected.status_code == 400
    assert not lifecycle.audit_path.exists()

    created = await client.post(
        "/api/llm/pricing",
        headers=_headers(),
        json={"expected_revision": 0, "version": _version()},
    )
    assert created.status_code == 201

    records = [
        json.loads(line)
        for line in lifecycle.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["component"] == "llm_pricing"
    assert records[0]["operation"] == "create"
    assert records[0]["outcome"] == "success"
    assert len(records[0]["subject_digest"]) == 64
    audit_text = lifecycle.audit_path.read_text(encoding="utf-8")
    assert "pricing-operator" not in audit_text
    assert "input_per_million" not in audit_text


@pytest.mark.asyncio
async def test_pricing_internal_failure_is_generic_and_not_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client, catalog, lifecycle = _make_api(tmp_path, scopes=["*"])

    def fail_add(*args, **kwargs):
        raise OSError("private catalog path must not escape")

    monkeypatch.setattr(catalog, "add", fail_add)
    response = await client.post(
        "/api/llm/pricing",
        headers=_headers(),
        json={"expected_revision": 0, "version": _version()},
    )

    assert response.status_code == 500
    assert await response.get_json() == {"error": "Price catalog operation failed"}
    assert "private catalog path" not in (await response.get_data(as_text=True))
    assert not lifecycle.audit_path.exists()


@pytest.mark.asyncio
async def test_pricing_routes_accept_and_return_the_display_name(tmp_path: Path):
    """显示名称必须能经 REST 写入并读回（需求 9）。

    `PriceVersion` 有这个字段、界面上有这个输入框，而 `_PRICE_VERSION_FIELDS`
    是一个**白名单**：漏掉它的话，前端提交会被 400「version contains unknown
    fields」挡住——而那条错误对填表的人毫无指向性，他填的每一个字段看起来都合法。

    这是新增模型字段时最容易漏的一处：模型改了、界面改了、测试也可能只测模型层，
    而请求根本到不了模型。
    """
    # 读与写是两个 scope：这条用例两样都要，因此用 `*`（与既有 CRUD 用例一致）。
    client, catalog, _ = _make_api(tmp_path, scopes=["*"])

    created = await client.post(
        "/api/llm/pricing",
        headers=_headers(),
        json={
            "expected_revision": catalog.revision,
            "version": _version("v-label", display_name="Claude Sonnet 5"),
        },
    )

    assert created.status_code == 201, await created.get_data(as_text=True)
    body = await created.get_json()
    assert body["data"]["version"]["display_name"] == "Claude Sonnet 5"

    listed = await client.get("/api/llm/pricing", headers=_headers())
    versions = (await listed.get_json())["data"]["versions"]
    assert {item["version_id"]: item["display_name"] for item in versions} == {
        "v-label": "Claude Sonnet 5"
    }


@pytest.mark.asyncio
async def test_pricing_routes_accept_an_omitted_display_name(tmp_path: Path):
    """不带这个字段照旧可用：老前端与老价目文件都不会提交它。"""
    client, catalog, _ = _make_api(tmp_path, scopes=["llm.pricing.manage"])

    created = await client.post(
        "/api/llm/pricing",
        headers=_headers(),
        json={"expected_revision": catalog.revision, "version": _version("v-plain")},
    )

    assert created.status_code == 201
    assert (await created.get_json())["data"]["version"]["display_name"] is None


@pytest.mark.asyncio
async def test_pricing_routes_reject_a_blank_display_name(tmp_path: Path):
    """空白标签会在表格里留下一行没有身份的价格，比没有标签更糟。"""
    client, catalog, _ = _make_api(tmp_path, scopes=["llm.pricing.manage"])

    response = await client.post(
        "/api/llm/pricing",
        headers=_headers(),
        json={
            "expected_revision": catalog.revision,
            "version": _version("v-blank", display_name="   "),
        },
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_editing_only_the_label_leaves_every_rate_untouched(tmp_path: Path):
    """改标签不得动到任何一档单价——历史账单必须稳定。

    这条走完整的 PUT 路径，而不是只调模型层：路由用 `model_validate` 整体替换
    版本，一个把标签写进错误字段的实现会在这里显形。
    """
    client, catalog, _ = _make_api(tmp_path, scopes=["*"])
    await client.post(
        "/api/llm/pricing",
        headers=_headers(),
        json={
            "expected_revision": catalog.revision,
            "version": _version("v-rates", display_name="旧标签"),
        },
    )
    before = (await (await client.get("/api/llm/pricing", headers=_headers())).get_json())[
        "data"
    ]["versions"][0]

    updated = await client.put(
        "/api/llm/pricing/v-rates",
        headers=_headers(),
        json={
            "expected_revision": catalog.revision,
            "version": _version("v-rates", display_name="新标签"),
        },
    )

    assert updated.status_code == 200
    after = (await (await client.get("/api/llm/pricing", headers=_headers())).get_json())[
        "data"
    ]["versions"][0]
    assert after["display_name"] == "新标签"
    for rate in (
        "input_per_million",
        "output_per_million",
        "cache_read_per_million",
        "cache_write_per_million",
    ):
        assert after[rate] == before[rate], f"{rate} 被改标签的操作改动了"
