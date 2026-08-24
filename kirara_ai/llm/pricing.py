"""Versioned provider pricing and immutable request cost snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kirara_ai.llm.format.response import Usage, UsageSource


class PriceVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    version_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effective_from: datetime
    currency: str = Field(min_length=3, max_length=3)
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    cache_read_per_million: Decimal = Field(ge=0)
    cache_write_per_million: Decimal = Field(ge=0)

    @field_validator("effective_from")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective_from must include a timezone")
        return value.astimezone(timezone.utc)


class CostSnapshot(BaseModel):
    """Immutable cost facts captured at request completion."""

    model_config = ConfigDict(frozen=True)

    price_version_id: str
    provider: str
    model: str
    currency: str
    priced_at: datetime
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    input_cost: Optional[Decimal] = None
    output_cost: Optional[Decimal] = None
    cache_read_cost: Optional[Decimal] = None
    cache_write_cost: Optional[Decimal] = None
    total_cost: Optional[Decimal] = None
    usage_source: UsageSource = UsageSource.UNKNOWN


class PriceCatalog:
    """Small in-memory catalog; persistence can serialize these validated models."""

    def __init__(self, versions: Iterable[PriceVersion] = ()):
        self._versions: dict[str, PriceVersion] = {}
        for version in versions:
            self.add(version)

    def add(self, version: PriceVersion) -> None:
        if version.version_id in self._versions:
            raise ValueError(f"duplicate price version: {version.version_id}")
        self._versions[version.version_id] = version

    def resolve(self, provider: str, model: str, requested_at: datetime) -> PriceVersion:
        requested_at = _as_utc(requested_at)
        matches = [
            version
            for version in self._versions.values()
            if version.provider == provider
            and version.model == model
            and version.effective_from <= requested_at
        ]
        if not matches:
            raise LookupError(f"no effective price for {provider}/{model}")
        return max(matches, key=lambda version: version.effective_from)

    def values(self) -> tuple[PriceVersion, ...]:
        return tuple(self._versions.values())


def calculate_cost_snapshot(
    usage: Usage,
    price: PriceVersion,
    *,
    requested_at: datetime,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> CostSnapshot:
    actual_provider = provider or price.provider
    actual_model = model or price.model
    if actual_provider != price.provider or actual_model != price.model:
        raise ValueError("price version does not match request provider or model")

    input_tokens = _input_tokens(usage)
    output_tokens = usage.completion_tokens
    cache_read_tokens = usage.cached_tokens
    cache_write_tokens = usage.cache_write_tokens

    input_cost = _cost(input_tokens, price.input_per_million)
    output_cost = _cost(output_tokens, price.output_per_million)
    cache_read_cost = _cost(cache_read_tokens, price.cache_read_per_million)
    cache_write_cost = _cost(cache_write_tokens, price.cache_write_per_million)
    dimensions = (input_cost, output_cost, cache_read_cost, cache_write_cost)
    total_cost = sum(dimensions, Decimal("0")) if all(value is not None for value in dimensions) else None

    return CostSnapshot(
        price_version_id=price.version_id,
        provider=price.provider,
        model=price.model,
        currency=price.currency,
        priced_at=_as_utc(requested_at),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        input_cost=input_cost,
        output_cost=output_cost,
        cache_read_cost=cache_read_cost,
        cache_write_cost=cache_write_cost,
        total_cost=total_cost,
        usage_source=usage.source,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value.astimezone(timezone.utc)


def _input_tokens(usage: Usage) -> Optional[int]:
    if usage.prompt_tokens is None:
        return None
    excluded = (usage.cached_tokens or 0) + (usage.cache_write_tokens or 0)
    return max(0, usage.prompt_tokens - excluded)


def _cost(tokens: Optional[int], rate: Decimal) -> Optional[Decimal]:
    if tokens is None:
        return None
    return (Decimal(tokens) * rate / Decimal("1000000")).quantize(Decimal("0.00000001"))
