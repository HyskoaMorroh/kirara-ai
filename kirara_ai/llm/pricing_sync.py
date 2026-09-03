"""从公开定价目录同步模型单价。

`PriceCatalog` 只管本地文件：`refresh()` 是「重读磁盘以感知别的进程写入」，
不拉远端。于是价格此前只能手工敲或导入 JSON——用户接入一个新上游后要自己
去翻官网价目表，再逐个模型录四个数字。最容易出事的是单位：官网常写「每千
token」，而本项目存的是 `*_per_million`，记错一次，成本统计整体偏一千倍，
而且界面上看不出任何异常。

这里对接的公开目录以每百万 token 计价，与本项目字段同单位，**不做换算**。
同步遵循三条：

1. 手工价优先。同步只碰自己写过的版本（`version_id` 带 `upstream:` 前缀），
   用户改过或自己新建的版本一律不动。
2. 数字没变就不落盘。目录一次回来两百多家供应商，若每轮都重写，`revision`
   会无意义地暴涨，还会把「价格变过」这个信号淹掉。
3. 网络失败不污染既有价格。拉取或解析炸了就原样返回错误，磁盘上的价格保持
   上一次的状态。
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable, Iterator, Optional

from kirara_ai.llm.pricing import PriceCatalog, PriceVersion
from kirara_ai.logger import get_logger

#: 公开定价目录。按供应商 -> 模型 -> cost 组织，cost 内是每百万 token 单价。
UPSTREAM_CATALOG_URL = "https://models.dev/api.json"

#: 同步写入的版本号前缀。手工版本不带它，因此不会被自动同步覆盖。
UPSTREAM_VERSION_PREFIX = "upstream:"

#: 目录整份 4MB 上下，给足余量但不允许无上限读，避免异常响应吃满内存。
MAX_DOCUMENT_BYTES = 32 * 1024 * 1024

DEFAULT_FETCH_TIMEOUT_SECONDS = 30.0

logger = get_logger("PriceSync")


@dataclass(frozen=True)
class UpstreamPriceEntry:
    """目录里一个模型的四档单价，单位为每百万 token。"""

    key: str
    provider: str
    model: str
    provider_name: str
    #: 上游为这个模型给出的可读名（目录里每个模型都带 `name`）。
    #:
    #: `None` 表示上游没给。**不回落到 provider 的 `name`**：那会让同一家的
    #: 所有模型都显示成「Anthropic」，比没有标签更容易读错。
    display_name: Optional[str]
    input_per_million: Decimal
    output_per_million: Decimal
    cache_read_per_million: Decimal
    cache_write_per_million: Decimal

    @property
    def version_id(self) -> str:
        return f"{UPSTREAM_VERSION_PREFIX}{self.key}"


@dataclass
class PriceSyncReport:
    """一轮同步的结果。`error` 非空时其余计数一律为 0。"""

    imported: int = 0
    unchanged: int = 0
    skipped_manual: int = 0
    error: Optional[str] = None
    synced_at: Optional[datetime] = None
    changed_models: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "imported": self.imported,
            "unchanged": self.unchanged,
            "skipped_manual": self.skipped_manual,
            "error": self.error,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
            "changed_models": list(self.changed_models),
        }


def _as_decimal(value: object) -> Optional[Decimal]:
    """把目录里的数字转成 Decimal；不是有限数就返回 None。"""

    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def parse_upstream_catalog(document: object) -> Iterator[UpstreamPriceEntry]:
    """把目录文档摊平成逐模型的价格条目。

    结构不对的供应商或模型条目直接跳过——目录由社区维护，一个坏条目不该让
    整轮同步失败。完全没有 input 也没有 output 的模型不产出条目，否则会在
    价目表里落下一堆 0 元模型，让「未配置价格」和「免费」混为一谈。
    """

    if not isinstance(document, dict):
        return

    for provider_id, provider in document.items():
        if not isinstance(provider_id, str) or not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        provider_name = provider.get("name")
        if not isinstance(provider_name, str) or not provider_name:
            provider_name = provider_id

        for model_id, model in models.items():
            if not isinstance(model_id, str) or not isinstance(model, dict):
                continue
            cost = model.get("cost")
            if not isinstance(cost, dict):
                continue

            input_price = _as_decimal(cost.get("input"))
            output_price = _as_decimal(cost.get("output"))
            if input_price is None and output_price is None:
                continue

            # 上游给的可读名。类型不对时当作没给——目录由社区维护，
            # `str(123)` 会在价目表里落一个假标签。
            model_name = model.get("name")
            if not isinstance(model_name, str) or not model_name.strip():
                model_name = None
            else:
                model_name = model_name.strip()

            yield UpstreamPriceEntry(
                key=f"{provider_id}/{model_id}",
                provider=provider_id,
                model=model_id,
                provider_name=provider_name,
                display_name=model_name,
                input_per_million=input_price or Decimal("0"),
                output_per_million=output_price or Decimal("0"),
                cache_read_per_million=_as_decimal(cost.get("cache_read")) or Decimal("0"),
                cache_write_per_million=_as_decimal(cost.get("cache_write")) or Decimal("0"),
            )


def fetch_upstream_catalog(
    url: str = UPSTREAM_CATALOG_URL,
    *,
    timeout: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
) -> dict:
    """拉取并解析目录。仅接受 https，避免被配置成明文回源。"""

    if not url.lower().startswith("https://"):
        raise ValueError("price catalog url must use https")

    request = urllib.request.Request(  # noqa: S310 - scheme 已在上面限定为 https
        url,
        headers={"Accept": "application/json", "User-Agent": "kirara-ai-price-sync"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        status = getattr(response, "status", 200)
        if status != 200:
            raise RuntimeError(f"price catalog responded with HTTP {status}")
        raw = response.read(MAX_DOCUMENT_BYTES + 1)

    if len(raw) > MAX_DOCUMENT_BYTES:
        raise RuntimeError("price catalog response exceeded the size limit")

    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("price catalog document is not an object")
    return document


class UpstreamPriceSyncer:
    """把公开目录里的单价写进 `PriceCatalog`，只碰自己写过的版本。"""

    def __init__(
        self,
        *,
        fetch: Optional[Callable[[], dict]] = None,
        currency: str = "USD",
    ) -> None:
        self._fetch = fetch or fetch_upstream_catalog
        self._currency = currency

    async def sync(
        self,
        catalog: PriceCatalog,
        *,
        only: Optional[Iterable[str]] = None,
    ) -> PriceSyncReport:
        """跑一轮同步。

        :param only: 限定同步的 `provider/model` 键；为空表示整份目录。
        """

        try:
            document = await asyncio.to_thread(self._fetch)
        except Exception as error:  # noqa: BLE001 - 任何拉取失败都只记录，不抛
            logger.warning(f"Price catalog sync failed to fetch upstream: {error}")
            return PriceSyncReport(error=str(error) or error.__class__.__name__)

        try:
            entries = list(parse_upstream_catalog(document))
        except Exception as error:  # noqa: BLE001
            logger.warning(f"Price catalog sync failed to parse upstream: {error}")
            return PriceSyncReport(error=str(error) or error.__class__.__name__)

        wanted = set(only) if only is not None else None
        if wanted is not None:
            entries = [entry for entry in entries if entry.key in wanted]

        return await asyncio.to_thread(self._apply, catalog, entries)

    def _apply(
        self, catalog: PriceCatalog, entries: list[UpstreamPriceEntry]
    ) -> PriceSyncReport:
        report = PriceSyncReport(synced_at=datetime.now(timezone.utc))
        existing = {version.version_id: version for version in catalog.values()}
        manual_keys = {
            (version.provider, version.model)
            for version in existing.values()
            if not version.version_id.startswith(UPSTREAM_VERSION_PREFIX)
        }

        for entry in entries:
            # 用户手工维护过这个模型的价格，同步不插手。
            if (entry.provider, entry.model) in manual_keys:
                report.skipped_manual += 1
                continue

            current = existing.get(entry.version_id)
            if current is not None and self._is_same_price(current, entry):
                report.unchanged += 1
                continue

            version = PriceVersion(
                version_id=entry.version_id,
                provider=entry.provider,
                model=entry.model,
                display_name=entry.display_name,
                effective_from=report.synced_at,
                currency=self._currency,
                input_per_million=entry.input_per_million,
                output_per_million=entry.output_per_million,
                cache_read_per_million=entry.cache_read_per_million,
                cache_write_per_million=entry.cache_write_per_million,
            )
            try:
                if current is None:
                    catalog.add(version)
                else:
                    catalog.update(version)
            except Exception as error:  # noqa: BLE001 - 单个模型写失败不该中断整轮
                logger.warning(
                    f"Price catalog sync skipped {entry.key}: {error}"
                )
                continue

            report.imported += 1
            report.changed_models.append(entry.key)

        return report

    @staticmethod
    def _is_same_price(version: PriceVersion, entry: UpstreamPriceEntry) -> bool:
        return (
            version.input_per_million == entry.input_per_million
            and version.output_per_million == entry.output_per_million
            and version.cache_read_per_million == entry.cache_read_per_million
            and version.cache_write_per_million == entry.cache_write_per_million
        )
