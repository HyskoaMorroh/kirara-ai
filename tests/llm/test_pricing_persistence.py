from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from filelock import FileLock

import pytest

from kirara_ai.llm.format.response import Usage, UsageSource
from kirara_ai.llm.pricing import (
    PriceCatalog,
    PriceCatalogConflictError,
    PriceCatalogIntegrityError,
    PriceCatalogLockError,
    PriceVersion,
    calculate_cost_snapshot,
)
from kirara_ai.workflow.persistence import FileTransaction


def at(hour: int) -> datetime:
    return datetime(2026, 8, 27, hour, tzinfo=timezone.utc)


def price(
    version_id: str,
    effective_hour: int,
    input_rate: str,
    *,
    provider: str = "provider-a",
    model: str = "model-a",
) -> PriceVersion:
    return PriceVersion(
        version_id=version_id,
        provider=provider,
        model=model,
        effective_from=at(effective_hour),
        currency="USD",
        input_per_million=Decimal(input_rate),
        output_per_million=Decimal("12"),
        cache_read_per_million=Decimal("1"),
        cache_write_per_million=Decimal("3"),
    )


def test_catalog_save_load_and_persistent_add_keep_the_memory_api(tmp_path: Path):
    catalog_path = tmp_path / "pricing.json"
    catalog = PriceCatalog([price("v1", 8, "4")])

    catalog.add(price("v2", 10, "8"))
    assert [item.version_id for item in catalog.values()] == ["v1", "v2"]
    assert catalog.resolve("provider-a", "model-a", at(11)).version_id == "v2"

    catalog.save(catalog_path)
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "kirara-ai.price-catalog"
    assert payload["format_version"] == 1
    assert [item["version_id"] for item in payload["versions"]] == ["v1", "v2"]

    loaded = PriceCatalog.load(catalog_path)
    loaded.add(price("v3", 12, "9"))

    reopened = PriceCatalog.load(catalog_path)
    assert [item.version_id for item in reopened.values()] == ["v1", "v2", "v3"]
    assert reopened.resolve("provider-a", "model-a", at(13)).version_id == "v3"


def test_export_and_import_merge_validated_versions(tmp_path: Path):
    export_path = tmp_path / "exported-pricing.json"
    target_path = tmp_path / "target-pricing.json"
    source = PriceCatalog(
        [
            price("a-v1", 8, "4"),
            price("b-v1", 9, "5", provider="provider-b", model="model-b"),
        ]
    )
    target = PriceCatalog([price("a-v0", 6, "3")])
    target.save(target_path)

    source.export_to(export_path)
    target.import_from(export_path)

    assert [item.version_id for item in target.values()] == ["a-v0", "a-v1", "b-v1"]
    assert target.resolve("provider-b", "model-b", at(10)).version_id == "b-v1"
    assert [item.version_id for item in PriceCatalog.load(target_path).values()] == [
        "a-v0",
        "a-v1",
        "b-v1",
    ]


def test_export_does_not_delete_an_existing_sidecar_backup(tmp_path: Path):
    export_path = tmp_path / "exported-pricing.json"
    backup_path = Path(f"{export_path}.bak")
    backup_path.write_text("keep this backup", encoding="utf-8")

    PriceCatalog([price("v1", 8, "4")]).export_to(export_path)

    assert backup_path.read_text(encoding="utf-8") == "keep this backup"


def test_restore_backup_works_when_the_current_catalog_file_is_missing(tmp_path: Path):
    catalog_path = tmp_path / "pricing.json"
    backup_path = Path(f"{catalog_path}.bak")
    PriceCatalog([price("v1", 8, "4")]).export_to(backup_path)
    catalog = PriceCatalog()

    catalog.restore_backup(catalog_path)

    assert [item.version_id for item in catalog.values()] == ["v1"]
    assert catalog_path.is_file()
    assert not backup_path.exists()


def test_import_rejects_duplicate_ids_and_effective_time_conflicts(tmp_path: Path):
    duplicate_path = tmp_path / "duplicate.json"
    conflict_path = tmp_path / "conflict.json"
    target = PriceCatalog([price("v1", 8, "4")])
    PriceCatalog([price("v1", 10, "5")]).export_to(duplicate_path)
    PriceCatalog([price("different-id", 8, "9")]).export_to(conflict_path)

    with pytest.raises(ValueError, match="duplicate price version"):
        target.import_from(duplicate_path)
    with pytest.raises(ValueError, match="conflicting price version"):
        target.import_from(conflict_path)
    with pytest.raises(ValueError, match="conflicting price version"):
        target.add(price("another-id", 8, "10"))

    assert target.values() == (price("v1", 8, "4"),)


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("schema", "other.price-catalog", "schema"),
        ("schema_version", 2, "schema version"),
        ("format_version", 2, "format version"),
    ],
)
def test_import_rejects_unsupported_document_versions(
    tmp_path: Path, field: str, value: object, error: str
):
    import_path = tmp_path / f"invalid-{field}.json"
    payload = {
        "schema": "kirara-ai.price-catalog",
        "schema_version": 1,
        "format_version": 1,
        "versions": [price("v2", 10, "5").model_dump(mode="json")],
    }
    payload[field] = value
    import_path.write_text(json.dumps(payload), encoding="utf-8")
    catalog = PriceCatalog([price("v1", 8, "4")])

    with pytest.raises(ValueError, match=error):
        catalog.import_from(import_path)

    assert [item.version_id for item in catalog.values()] == ["v1"]


def test_restore_backup_is_atomic_and_does_not_reprice_existing_snapshots(tmp_path: Path):
    catalog_path = tmp_path / "pricing.json"
    original_price = price("v1", 8, "4")
    catalog = PriceCatalog([original_price])
    catalog.save(catalog_path)
    snapshot = calculate_cost_snapshot(
        Usage(
            prompt_tokens=1_000_000,
            completion_tokens=500_000,
            cached_tokens=0,
            cache_write_tokens=0,
            source=UsageSource.PROVIDER,
        ),
        original_price,
        requested_at=at(9),
    )

    catalog.add(price("v2", 10, "20"))
    backup_path = Path(f"{catalog_path}.bak")
    assert backup_path.is_file()
    assert catalog.resolve("provider-a", "model-a", at(11)).version_id == "v2"

    catalog.restore_backup()

    assert [item.version_id for item in catalog.values()] == ["v1"]
    assert [item.version_id for item in PriceCatalog.load(catalog_path).values()] == ["v1"]
    assert [item.version_id for item in PriceCatalog.load(backup_path).values()] == ["v1", "v2"]
    assert snapshot.price_version_id == "v1"
    assert snapshot.input_cost == Decimal("4")
    assert snapshot.output_cost == Decimal("6")
    assert snapshot.total_cost == Decimal("10")


def test_failed_persistent_add_keeps_memory_and_disk_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    catalog_path = (tmp_path / "pricing.json").resolve()
    catalog = PriceCatalog([price("v1", 8, "4")])
    catalog.save(catalog_path)
    original_bytes = catalog_path.read_bytes()
    original_publish = FileTransaction._publish_entry
    backup_path = Path(f"{catalog_path}.bak").resolve()

    def fail_backup_publish(entry: dict[str, object]) -> None:
        if Path(str(entry["target"])).resolve() == backup_path:
            raise OSError("simulated backup publish failure")
        original_publish(entry)

    monkeypatch.setattr(
        FileTransaction,
        "_publish_entry",
        staticmethod(fail_backup_publish),
    )

    with pytest.raises(OSError, match="simulated backup publish failure"):
        catalog.add(price("v2", 10, "8"))

    assert [item.version_id for item in catalog.values()] == ["v1"]
    assert catalog_path.read_bytes() == original_bytes
    assert [item.version_id for item in PriceCatalog.load(catalog_path).values()] == ["v1"]


def test_load_or_create_publishes_revision_zero_and_canonical_integrity(tmp_path: Path):
    catalog_path = tmp_path / "pricing" / "catalog.json"

    catalog = PriceCatalog.load_or_create(catalog_path)

    assert catalog.revision == 0
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert payload["revision"] == 0
    assert payload["integrity"] == PriceCatalog.compute_integrity(payload)
    assert PriceCatalog.load(catalog_path).revision == 0


def test_persistent_mutation_increments_once_and_rejects_stale_revision(tmp_path: Path):
    catalog_path = tmp_path / "catalog.json"
    first = PriceCatalog.load_or_create(catalog_path)
    second = PriceCatalog.load(catalog_path)

    first.add(price("v1", 8, "4"), expected_revision=0)

    assert first.revision == 1
    assert PriceCatalog.load(catalog_path).revision == 1
    with pytest.raises(PriceCatalogConflictError, match="revision"):
        second.add(price("v2", 9, "5"), expected_revision=0)
    assert [item.version_id for item in PriceCatalog.load(catalog_path).values()] == ["v1"]


def test_structured_document_export_and_import_validates_integrity_and_cas(
    tmp_path: Path,
):
    catalog_path = tmp_path / "catalog.json"
    catalog = PriceCatalog.load_or_create(catalog_path)
    catalog.add(price("v1", 8, "4"), expected_revision=0)

    exported = catalog.export_document()

    imported = PriceCatalog.load_or_create(tmp_path / "imported.json")
    imported.import_document(exported, expected_revision=0)

    assert imported.revision == 1
    assert [item.version_id for item in imported.values()] == ["v1"]
    tampered = dict(exported)
    tampered["integrity"] = "0" * 64
    with pytest.raises(PriceCatalogIntegrityError, match="integrity"):
        imported.import_document(tampered, expected_revision=1)
    assert [item.version_id for item in imported.values()] == ["v1"]


def test_structured_document_import_rejects_stale_revision_without_mutating_catalog(
    tmp_path: Path,
):
    catalog_path = tmp_path / "catalog.json"
    catalog = PriceCatalog.load_or_create(catalog_path)
    catalog.add(price("v1", 8, "4"), expected_revision=0)
    document = PriceCatalog([price("v2", 9, "5")]).export_document()

    with pytest.raises(PriceCatalogConflictError, match="revision"):
        catalog.import_document(document, expected_revision=0)

    assert catalog.revision == 1
    assert [item.version_id for item in catalog.values()] == ["v1"]


def test_catalog_rejects_tampered_versions_without_matching_integrity(tmp_path: Path):
    catalog_path = tmp_path / "catalog.json"
    catalog = PriceCatalog.load_or_create(catalog_path)
    catalog.add(price("v1", 8, "4"), expected_revision=0)
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    payload["versions"][0]["input_per_million"] = "999"
    catalog_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PriceCatalogIntegrityError, match="integrity"):
        PriceCatalog.load(catalog_path)


def test_catalog_rotates_three_backup_generations_and_restores_selected_generation(
    tmp_path: Path,
):
    catalog_path = tmp_path / "catalog.json"
    catalog = PriceCatalog.load_or_create(catalog_path)
    for revision in range(4):
        catalog.add(
            price(f"v{revision + 1}", 8 + revision, str(revision + 1)),
            expected_revision=revision,
        )

    assert catalog.revision == 4
    assert all(Path(f"{catalog_path}.bak.{generation}").is_file() for generation in (1, 2, 3))
    assert [item.version_id for item in PriceCatalog.load(Path(f"{catalog_path}.bak.1")).values()] == [
        "v1",
        "v2",
        "v3",
    ]
    assert [item.version_id for item in PriceCatalog.load(Path(f"{catalog_path}.bak.3")).values()] == ["v1"]

    catalog.restore_backup(generation=2, expected_revision=4)

    assert catalog.revision == 5
    assert [item.version_id for item in catalog.values()] == ["v1", "v2"]
    assert PriceCatalog.load(catalog_path).revision == 5


def test_catalog_instances_cannot_overwrite_each_other_without_a_fresh_revision(
    tmp_path: Path,
):
    catalog_path = tmp_path / "catalog.json"
    first = PriceCatalog.load_or_create(catalog_path)
    second = PriceCatalog.load(catalog_path)

    first.add(price("v1", 8, "4"))

    with pytest.raises(PriceCatalogConflictError, match="changed"):
        second.add(price("v2", 9, "5"))

    assert second.refresh() == 1
    assert [item.version_id for item in second.values()] == ["v1"]
    second.add(price("v2", 9, "5"), expected_revision=1)
    assert [item.version_id for item in PriceCatalog.load(catalog_path).values()] == [
        "v1",
        "v2",
    ]


def test_catalog_lock_timeout_is_explicit_and_bounded(tmp_path: Path):
    catalog_path = tmp_path / "catalog.json"
    catalog = PriceCatalog.load_or_create(catalog_path, lock_timeout=0.05)
    lock = FileLock(f"{catalog_path}.lock")
    lock.acquire()
    try:
        with pytest.raises(PriceCatalogLockError, match="lock"):
            catalog.add(price("v1", 8, "4"), expected_revision=0)
    finally:
        lock.release()
