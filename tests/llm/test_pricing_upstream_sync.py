"""上游公开定价目录同步。

价格此前只能手工填或导入 JSON：`PriceCatalog.refresh()` 是「重读本地文件以
感知别的进程写入」，不拉远端。用户新增一个上游后，得自己去翻官网价目表，
再逐个模型敲四个数字——一旦记错单位（每千 vs 每百万），成本统计会整体偏
一千倍且没有任何提示。

这里锁住同步器的行为：单位不换算（上游本身就是每百万）、缺失字段按 0、
只在数字真的变了时才落盘、以及网络失败不能污染既有价格。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kirara_ai.llm.pricing import PriceCatalog, PriceVersion
from kirara_ai.llm.pricing_sync import (
    UpstreamPriceSyncer,
    parse_upstream_catalog,
)


def _document() -> dict:
    return {
        "anthropic": {
            "name": "Anthropic",
            "models": {
                "claude-sonnet-5": {
                    "name": "Claude Sonnet 5",
                    "cost": {
                        "input": 2,
                        "output": 10,
                        "cache_read": 0.2,
                        "cache_write": 2.5,
                    },
                },
            },
        },
        "openai": {
            "name": "OpenAI",
            "models": {
                # 真实响应里 openai 条目没有 cache_write。
                "gpt-4o": {"cost": {"input": 2.5, "output": 10, "cache_read": 1.25}},
                # 没有任何价格的条目要跳过，不能落成一堆 0 元模型。
                "gpt-4o-audio-preview": {"cost": {}},
                "o1-preview": {},
            },
        },
        # 结构不对的供应商不能让整轮同步崩掉。
        "broken": "not-a-mapping",
    }


def test_parser_keeps_the_upstream_per_million_unit_without_rescaling():
    entries = {entry.key: entry for entry in parse_upstream_catalog(_document())}

    sonnet = entries["anthropic/claude-sonnet-5"]
    assert sonnet.input_per_million == Decimal("2")
    assert sonnet.output_per_million == Decimal("10")
    assert sonnet.cache_read_per_million == Decimal("0.2")
    assert sonnet.cache_write_per_million == Decimal("2.5")


def test_parser_defaults_a_missing_cache_write_to_zero():
    entries = {entry.key: entry for entry in parse_upstream_catalog(_document())}

    assert entries["openai/gpt-4o"].cache_write_per_million == Decimal("0")


def test_parser_skips_models_that_carry_no_price_at_all():
    keys = {entry.key for entry in parse_upstream_catalog(_document())}

    assert "openai/gpt-4o-audio-preview" not in keys
    assert "openai/o1-preview" not in keys


def test_parser_tolerates_a_malformed_provider_entry():
    keys = {entry.key for entry in parse_upstream_catalog(_document())}

    assert "anthropic/claude-sonnet-5" in keys
    assert not any(key.startswith("broken/") for key in keys)


@pytest.mark.asyncio
async def test_sync_writes_new_versions_into_the_catalog(tmp_path):
    catalog = PriceCatalog.load_or_create(tmp_path / "pricing.json")
    syncer = UpstreamPriceSyncer(fetch=lambda: _document())

    report = await syncer.sync(catalog)

    assert report.imported == 2
    assert report.error is None
    priced = {(version.provider, version.model) for version in catalog.values()}
    assert ("anthropic", "claude-sonnet-5") in priced
    assert ("openai", "gpt-4o") in priced


@pytest.mark.asyncio
async def test_sync_is_a_no_op_when_the_upstream_numbers_did_not_move(tmp_path):
    catalog = PriceCatalog.load_or_create(tmp_path / "pricing.json")
    syncer = UpstreamPriceSyncer(fetch=lambda: _document())

    first = await syncer.sync(catalog)
    revision_after_first = catalog.revision
    second = await syncer.sync(catalog)

    assert first.imported == 2
    assert second.imported == 0
    assert second.unchanged == 2
    assert catalog.revision == revision_after_first


@pytest.mark.asyncio
async def test_sync_leaves_a_manual_price_alone(tmp_path):
    """手工价优先。用户改过的数字不能被一次自动同步悄悄盖掉。"""
    catalog = PriceCatalog.load_or_create(tmp_path / "pricing.json")
    catalog.add(
        PriceVersion(
            version_id="manual-sonnet",
            provider="anthropic",
            model="claude-sonnet-5",
            effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            currency="USD",
            input_per_million=Decimal("99"),
            output_per_million=Decimal("99"),
            cache_read_per_million=Decimal("0"),
            cache_write_per_million=Decimal("0"),
        )
    )
    syncer = UpstreamPriceSyncer(fetch=lambda: _document())

    await syncer.sync(catalog)

    manual = next(
        version for version in catalog.values() if version.version_id == "manual-sonnet"
    )
    assert manual.input_per_million == Decimal("99")


@pytest.mark.asyncio
async def test_a_failed_fetch_reports_the_error_and_keeps_prices_intact(tmp_path):
    catalog = PriceCatalog.load_or_create(tmp_path / "pricing.json")
    await UpstreamPriceSyncer(fetch=lambda: _document()).sync(catalog)
    revision_before = catalog.revision

    def explode() -> dict:
        raise TimeoutError("upstream unreachable")

    report = await UpstreamPriceSyncer(fetch=explode).sync(catalog)

    assert report.imported == 0
    assert report.error is not None
    assert "unreachable" in report.error
    assert catalog.revision == revision_before
