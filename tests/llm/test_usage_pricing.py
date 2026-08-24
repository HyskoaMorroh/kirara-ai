from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kirara_ai.llm.format.response import Usage, UsageSource
from kirara_ai.llm.pricing import PriceCatalog, PriceVersion, calculate_cost_snapshot
from kirara_ai.llm.resilience import ProviderAttempt


def at(hour: int) -> datetime:
    return datetime(2026, 8, 23, hour, tzinfo=timezone.utc)


def price(version: str, effective_hour: int, input_rate: str) -> PriceVersion:
    return PriceVersion(
        version_id=version,
        provider="provider-a",
        model="model-a",
        effective_from=at(effective_hour),
        currency="USD",
        input_per_million=Decimal(input_rate),
        output_per_million=Decimal("12"),
        cache_read_per_million=Decimal("1"),
        cache_write_per_million=Decimal("3"),
    )


def test_usage_keeps_missing_values_unknown_and_records_source():
    usage = Usage(prompt_tokens=100)

    assert usage.source is UsageSource.UNKNOWN
    assert usage.completion_tokens is None
    assert usage.cached_tokens is None
    assert usage.cache_write_tokens is None

    reported = Usage(
        prompt_tokens=100,
        completion_tokens=25,
        total_tokens=125,
        source=UsageSource.PROVIDER,
    )
    assert reported.source is UsageSource.PROVIDER


def test_ttft_requires_a_real_first_chunk_timestamp():
    non_stream = ProviderAttempt(
        trace_id="trace",
        model="model-a",
        provider="provider-a",
        attempt=1,
        retry_index=0,
        success=True,
        started_at=10.0,
        completed_at=12.0,
    )
    stream = ProviderAttempt(
        trace_id="trace",
        model="model-a",
        provider="provider-a",
        attempt=2,
        retry_index=0,
        success=True,
        started_at=20.0,
        first_byte_at=20.25,
        completed_at=21.0,
    )

    assert non_stream.ttft_seconds is None
    assert stream.ttft_seconds == pytest.approx(0.25)


def test_price_catalog_selects_effective_version_and_snapshots_are_immutable():
    catalog = PriceCatalog([price("v1", 8, "4")])
    usage = Usage(
        prompt_tokens=1_000_000,
        completion_tokens=500_000,
        cached_tokens=200_000,
        cache_write_tokens=100_000,
        source=UsageSource.PROVIDER,
    )

    selected = catalog.resolve("provider-a", "model-a", at(9))
    snapshot = calculate_cost_snapshot(usage, selected, requested_at=at(9))
    catalog.add(price("v2", 10, "8"))

    assert catalog.resolve("provider-a", "model-a", at(9)).version_id == "v1"
    assert catalog.resolve("provider-a", "model-a", at(11)).version_id == "v2"
    assert snapshot.price_version_id == "v1"
    assert snapshot.input_tokens == 700_000
    assert snapshot.input_cost == Decimal("2.8")
    assert snapshot.output_cost == Decimal("6")
    assert snapshot.cache_read_cost == Decimal("0.2")
    assert snapshot.cache_write_cost == Decimal("0.3")
    assert snapshot.total_cost == Decimal("9.3")


def test_unknown_billable_usage_never_becomes_zero_cost():
    snapshot = calculate_cost_snapshot(
        Usage(source=UsageSource.UNKNOWN),
        price("v1", 8, "4"),
        requested_at=at(9),
    )

    assert snapshot.total_cost is None
    assert snapshot.input_cost is None
    assert snapshot.output_cost is None


def test_price_versions_reject_duplicate_ids_and_model_mismatch():
    catalog = PriceCatalog([price("v1", 8, "4")])

    with pytest.raises(ValueError, match="duplicate price version"):
        catalog.add(price("v1", 9, "5"))

    with pytest.raises(ValueError, match="does not match"):
        calculate_cost_snapshot(
            Usage(prompt_tokens=1, completion_tokens=1),
            price("v1", 8, "4").model_copy(update={"model": "other"}),
            provider="provider-a",
            model="model-a",
            requested_at=at(9),
        )
