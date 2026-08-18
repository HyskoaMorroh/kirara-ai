from pathlib import Path

from kirara_ai.workflow.presets import PRESETS_DIR
from kirara_ai.workflow.presets.catalog import (
    DispatchTriggerExample,
    load_preset_catalog,
    validate_preset_catalog,
)


def test_catalog_covers_every_bundled_yaml_with_discoverability_metadata():
    catalog = load_preset_catalog()
    yaml_ids = {
        f"{path.parent.name}:{path.stem}"
        for path in Path(PRESETS_DIR).glob("*/*.yaml")
    }

    assert {entry.id for entry in catalog.presets} == yaml_ids
    assert len(catalog.presets) == len(yaml_ids)
    for entry in catalog.presets:
        assert entry.name_zh
        assert entry.purpose
        assert entry.prerequisites is not None
        assert entry.trigger_examples
        assert entry.capabilities
        assert entry.difficulty in {"beginner", "intermediate", "advanced"}
        assert entry.skill.workflow_id == entry.id
        assert entry.skill.version
        for example in entry.trigger_examples:
            DispatchTriggerExample.model_validate(example.model_dump())


def test_catalog_references_valid_yaml_and_dispatch_previews():
    issues = validate_preset_catalog(load_preset_catalog())
    assert issues == []
