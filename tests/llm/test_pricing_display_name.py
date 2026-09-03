"""定价条目要能带一个「显示名称」，而它绝不能参与计价匹配（需求 9）。

需求 9 点名「成本定价」。参考界面的定价表有两列身份：**模型标识**（等宽字体、
稳定的上游 ID）与**显示名称**（可读化展示），笔记里为此专门写了一句边界：
「模型标识使用稳定的上游模型 ID，显示名称单独保存，**不能用显示名称代替路由
匹配键**」（`docs/superpowers/plans/ccs-ui-notes.md` 的
`Image_2026-08-23_032940_110` 一节）。

本项目此前只有 `model` 一个字段。这在有几条价格时不是问题，在几十条时是：
`anthropic/claude-sonnet-5` 与 `anthropic/claude-sonnet-5-20260514` 在表格里
只差一个后缀，而它们的单价可能不同。要在一屏里挑出「哪一行是我在用的那个」，
唯一可读的抓手正是显示名称。

同步进来的价格更是如此：上游目录**每个模型都带 `name`**（fixture 里
`"claude-sonnet-5": {"name": "Claude Sonnet 5", ...}`），此前解析器把它丢掉了——
明明有可读名字，界面上却只能看一串 ID。

三条边界，任何一条破了都比没有这个字段更糟：

1. **它不参与任何匹配。** 计价按 `(provider, model)` 找版本；显示名称只是标签。
   一旦哪里拿它当键，改一个标签就会让历史账单换一个价格。
2. **它可以缺省，且缺省不等于空串。** 老的价目文件没有这个字段，读进来必须
   照旧可用；显示时回落到 `model` 而不是显示一个空白单元格。
3. **它进摘要计算。** 目录有 `integrity` 自校验，新增字段若不进摘要，
   一次手工改标签就会让文件与摘要不一致，下一次载入直接失败。
   （这一条由 `compute_integrity` 对整个文档取哈希天然满足，
   但要有断言钉住——它是「新增字段」这件事最容易踩空的一处。）
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from kirara_ai.llm.pricing import PriceCatalog, PriceVersion
from kirara_ai.llm.pricing_sync import parse_upstream_catalog


def _version(**overrides) -> PriceVersion:
    payload = {
        "version_id": "v1",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "effective_from": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "currency": "USD",
        "input_per_million": Decimal("2"),
        "output_per_million": Decimal("10"),
        "cache_read_per_million": Decimal("0.2"),
        "cache_write_per_million": Decimal("2.5"),
    }
    payload.update(overrides)
    return PriceVersion(**payload)


class TestTheField:
    def test_a_display_name_can_be_stored(self):
        assert _version(display_name="Claude Sonnet 5").display_name == "Claude Sonnet 5"

    def test_it_defaults_to_none_not_an_empty_string(self):
        """`None` 是「没填」，空串是「填了一个空的」。

        两者混为一谈时，界面无法决定该回落到 `model` 还是该显示空白，
        而老的价目文件里这个字段本来就不存在。
        """
        assert _version().display_name is None

    def test_a_blank_display_name_is_refused(self):
        """空白标签是一个更糟的状态：表格里出现一个没有身份的行。"""
        for blank in ("", "   ", "\t"):
            with pytest.raises(ValueError):
                _version(display_name=blank)

    def test_it_is_bounded(self):
        """标签会进表格单元格，无界长度会把列宽撑破。"""
        with pytest.raises(ValueError):
            _version(display_name="x" * 300)


class TestItNeverAffectsPricing:
    def test_lookup_still_matches_on_provider_and_model(self, tmp_path: Path):
        """计价按 `(provider, model)` 找版本，显示名称不参与。"""
        catalog = PriceCatalog.load_or_create(tmp_path / "pricing.json")
        catalog.add(_version(display_name="一个完全无关的标签"))

        found = catalog.resolve("anthropic", "claude-sonnet-5", datetime.now(timezone.utc))

        assert found is not None
        assert found.version_id == "v1"

    def test_two_versions_cannot_share_an_identity_just_because_labels_differ(
        self, tmp_path: Path
    ):
        """标签不同不足以让同一 `(provider, model, effective_from)` 共存。

        如果哪里把显示名称并进了身份，这条会通过——而那意味着同一个模型在
        同一时刻有两个价格，计价取哪一个由字典顺序决定。
        """
        catalog = PriceCatalog.load_or_create(tmp_path / "pricing.json")
        catalog.add(_version(display_name="标签 A"))

        with pytest.raises(Exception):
            catalog.add(_version(version_id="v2", display_name="标签 B"))

    def test_changing_only_the_label_does_not_change_the_price(self, tmp_path: Path):
        """改标签不得改动任何一档单价——历史账单必须稳定。"""
        path = tmp_path / "pricing.json"
        catalog = PriceCatalog.load_or_create(path)
        catalog.add(_version(display_name="旧标签"))

        reloaded = PriceCatalog.load_or_create(path)
        original = reloaded.resolve("anthropic", "claude-sonnet-5", datetime.now(timezone.utc))
        assert original is not None
        reloaded.update(_version(display_name="新标签"))

        after = PriceCatalog.load_or_create(path).resolve(
            "anthropic", "claude-sonnet-5", datetime.now(timezone.utc)
        )
        assert after is not None
        assert after.display_name == "新标签"
        assert after.input_per_million == original.input_per_million
        assert after.output_per_million == original.output_per_million
        assert after.cache_read_per_million == original.cache_read_per_million
        assert after.cache_write_per_million == original.cache_write_per_million


class TestPersistence:
    def test_it_survives_a_save_and_load(self, tmp_path: Path):
        path = tmp_path / "pricing.json"
        catalog = PriceCatalog.load_or_create(path)
        catalog.add(_version(display_name="Claude Sonnet 5"))

        reloaded = PriceCatalog.load_or_create(path)

        assert reloaded.values()[0].display_name == "Claude Sonnet 5"

    def test_a_catalog_without_the_field_still_loads(self, tmp_path: Path):
        """老的价目文件没有这个字段，读进来必须照旧可用。

        新增字段最常见的破坏方式是把它做成必填：升级之后旧文件直接载入失败，
        而那时用户手里已经没有可用的价目表了。
        """
        import json

        path = tmp_path / "pricing.json"
        catalog = PriceCatalog.load_or_create(path)
        catalog.add(_version())
        document = json.loads(path.read_text(encoding="utf-8"))
        for entry in document["versions"]:
            entry.pop("display_name", None)
        document["integrity"] = PriceCatalog.compute_integrity(document)
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

        reloaded = PriceCatalog.load_or_create(path)

        assert reloaded.values()[0].display_name is None

    def test_the_label_is_covered_by_the_integrity_digest(self, tmp_path: Path):
        """改标签而不重算摘要，必须被完整性校验挡住。

        新增字段若不进摘要，一次手工改标签就会让文件与摘要不一致而无人发现，
        随后任何一次真实篡改也检测不出来。
        """
        import json

        from kirara_ai.llm.pricing import PriceCatalogIntegrityError

        path = tmp_path / "pricing.json"
        catalog = PriceCatalog.load_or_create(path)
        catalog.add(_version(display_name="原标签"))
        document = json.loads(path.read_text(encoding="utf-8"))
        document["versions"][0]["display_name"] = "被改过的标签"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(PriceCatalogIntegrityError):
            PriceCatalog.load_or_create(path)


class TestUpstreamSync:
    """上游目录每个模型都带 `name`，此前被丢掉了。"""

    @staticmethod
    def _document() -> dict:
        return {
            "anthropic": {
                "name": "Anthropic",
                "models": {
                    "claude-sonnet-5": {
                        "name": "Claude Sonnet 5",
                        "cost": {"input": 2, "output": 10},
                    },
                    # 没有 `name` 的条目照旧要能同步进来。
                    "claude-haiku-4-5": {"cost": {"input": 1, "output": 5}},
                },
            }
        }

    def test_the_upstream_model_name_is_kept(self):
        entries = {entry.key: entry for entry in parse_upstream_catalog(self._document())}

        assert entries["anthropic/claude-sonnet-5"].display_name == "Claude Sonnet 5"

    def test_a_model_without_a_name_yields_none(self):
        """缺 `name` 不能回落到 provider 的 `name`——那会让所有模型显示成
        「Anthropic」，比没有标签更容易读错。
        """
        entries = {entry.key: entry for entry in parse_upstream_catalog(self._document())}

        assert entries["anthropic/claude-haiku-4-5"].display_name is None

    def test_a_non_string_name_is_ignored_not_stringified(self):
        """目录由社区维护，字段类型不能假定。`str(123)` 会落一个假标签。"""
        document = self._document()
        document["anthropic"]["models"]["claude-sonnet-5"]["name"] = 123

        entries = {entry.key: entry for entry in parse_upstream_catalog(document)}

        assert entries["anthropic/claude-sonnet-5"].display_name is None

    def test_the_synced_version_carries_the_label(self, tmp_path: Path):
        """同步落盘的 `PriceVersion` 要带上标签，否则解析出来又丢在半路。"""
        from kirara_ai.llm.pricing_sync import UpstreamPriceSyncer

        path = tmp_path / "pricing.json"
        entries = list(parse_upstream_catalog(self._document()))
        catalog = PriceCatalog.load_or_create(path)
        report = UpstreamPriceSyncer()._apply(catalog, entries)
        assert report.imported == 2

        # 重新载入而不是复用内存对象：要证明标签**落了盘**，而不只是在内存里。
        labels = {
            version.model: version.display_name
            for version in PriceCatalog.load_or_create(path).values()
        }
        assert labels["claude-sonnet-5"] == "Claude Sonnet 5"
        assert labels["claude-haiku-4-5"] is None

    def test_a_changed_label_alone_is_not_treated_as_a_price_change(self, tmp_path: Path):
        """只有标签变了不算价格变化：每轮同步都重写会推高 revision，
        让乐观锁误判成有人在并发改价。
        """
        from kirara_ai.llm.pricing_sync import UpstreamPriceSyncer

        path = tmp_path / "pricing.json"
        entries = list(parse_upstream_catalog(self._document()))
        syncer = UpstreamPriceSyncer()
        syncer._apply(PriceCatalog.load_or_create(path), entries)

        renamed = self._document()
        renamed["anthropic"]["models"]["claude-sonnet-5"]["name"] = "Claude Sonnet 5 (new)"
        second = syncer._apply(
            PriceCatalog.load_or_create(path), list(parse_upstream_catalog(renamed))
        )

        assert second.imported == 0
        assert second.unchanged == 2
