"""Discoverability metadata and validation for bundled workflow presets."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator
from ruamel.yaml import YAML

from kirara_ai.im.message import IMMessage, MentionElement, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.block.registry import BlockRegistry
from kirara_ai.workflow.core.dispatch import CombinedDispatchRule
from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry
from kirara_ai.workflow.core.workflow.validation import validate_workflow_definition
from kirara_ai.workflow.implementations.blocks import register_system_blocks

from . import PRESETS_DIR

CATALOG_PATH = Path(__file__).with_name("catalog.json")


class DispatchTriggerExample(BaseModel):
    content: str = Field(max_length=2000)
    chat_type: Literal["私聊", "群聊"] = "私聊"
    mentioned: bool = False
    rule_groups: List[Dict[str, Any]] = Field(default_factory=list)


class SkillMetadata(BaseModel):
    """A versioned workflow template, executed by the existing workflow runtime."""

    version: str
    workflow_id: str
    inputs: List[str]
    outputs: List[str]
    prerequisites: List[str]
    examples: List[str]


class AgentMetadata(BaseModel):
    """Workflow composition plus policy metadata, not a separate executor."""

    workflow_id: str
    model_policy: Dict[str, Any] = Field(default_factory=dict)
    tool_policy: Dict[str, Any] = Field(default_factory=dict)
    memory_policy: Dict[str, Any] = Field(default_factory=dict)


class PresetCatalogEntry(BaseModel):
    id: str
    name_zh: str
    purpose: str
    prerequisites: List[str]
    trigger_examples: List[DispatchTriggerExample]
    capabilities: List[str]
    difficulty: Literal["beginner", "intermediate", "advanced"]
    skill: SkillMetadata
    agent: Optional[AgentMetadata] = None

    @model_validator(mode="after")
    def references_same_workflow(self):
        if self.skill.workflow_id != self.id:
            raise ValueError("skill.workflow_id must match the catalog id")
        if self.agent is not None and self.agent.workflow_id != self.id:
            raise ValueError("agent.workflow_id must match the catalog id")
        return self


class PresetCatalog(BaseModel):
    version: int = 1
    presets: List[PresetCatalogEntry]

    @model_validator(mode="after")
    def has_unique_ids(self):
        ids = [entry.id for entry in self.presets]
        if len(ids) != len(set(ids)):
            raise ValueError("catalog preset ids must be unique")
        return self


def load_preset_catalog(path: Path = CATALOG_PATH) -> PresetCatalog:
    with path.open("r", encoding="utf-8") as catalog_file:
        return PresetCatalog.model_validate(json.load(catalog_file))


def catalog_metadata(workflow_id: str) -> Optional[Dict[str, Any]]:
    entry = next(
        (item for item in load_preset_catalog().presets if item.id == workflow_id),
        None,
    )
    return entry.model_dump(mode="json") if entry is not None else None


def _preview_message(example: DispatchTriggerExample) -> IMMessage:
    sender = (
        ChatSender.from_group_chat("catalog-user", "catalog-group", "目录示例")
        if example.chat_type == "群聊"
        else ChatSender.from_c2c_chat("catalog-user", "目录示例")
    )
    elements = [TextMessage(example.content)]
    if example.mentioned:
        elements.insert(0, MentionElement(ChatSender.get_bot_sender()))
    return IMMessage(sender=sender, message_elements=elements)


def validate_preset_catalog(catalog: PresetCatalog) -> List[str]:
    """Validate YAML references through existing Block and dispatch preview logic."""
    issues: List[str] = []
    preset_root = Path(PRESETS_DIR)
    expected_ids = {
        f"{path.parent.name}:{path.stem}" for path in preset_root.glob("*/*.yaml")
    }
    catalog_ids = {entry.id for entry in catalog.presets}
    for missing in sorted(expected_ids - catalog_ids):
        issues.append(f"missing catalog entry: {missing}")
    for unknown in sorted(catalog_ids - expected_ids):
        issues.append(f"catalog references missing workflow: {unknown}")

    container = DependencyContainer()
    block_registry = BlockRegistry()
    container.register(BlockRegistry, block_registry)
    register_system_blocks(block_registry)
    workflow_registry = WorkflowRegistry(container)
    container.register(WorkflowRegistry, workflow_registry)

    yaml = YAML(typ="safe")
    for entry in catalog.presets:
        group_id, workflow_id = entry.id.split(":", 1)
        yaml_path = preset_root / group_id / f"{workflow_id}.yaml"
        if not yaml_path.is_file():
            continue
        try:
            raw = yaml.load(yaml_path.read_text(encoding="utf-8"))
            blocks = [
                SimpleNamespace(
                    name=block["name"],
                    type_name=block["type"],
                    config=block.get("params", {}),
                )
                for block in raw.get("blocks", [])
            ]
            wires = [
                SimpleNamespace(
                    source_block=block["name"],
                    source_output=connection["mapping"]["from"],
                    target_block=connection["target"],
                    target_input=connection["mapping"]["to"],
                )
                for block in raw.get("blocks", [])
                for connection in block.get("connected_to", [])
            ]
            validation_issues = validate_workflow_definition(
                blocks, wires, block_registry
            )
            errors = [item.code for item in validation_issues if item.severity == "error"]
            if errors:
                issues.append(f"invalid workflow {entry.id}: {','.join(errors)}")
                continue
            builder = WorkflowBuilder.load_from_yaml(str(yaml_path), container)
            workflow_registry.register(group_id, workflow_id, builder)
            for index, example in enumerate(entry.trigger_examples):
                rule = CombinedDispatchRule(
                    rule_id=f"catalog-{workflow_id}-{index}",
                    name="catalog preview",
                    workflow_id=entry.id,
                    rule_groups=example.rule_groups,
                )
                explanation = rule.explain_match(
                    _preview_message(example), workflow_registry, container
                )
                if explanation["matched"] is not True:
                    issues.append(
                        f"dispatch preview did not match: {entry.id} example {index}"
                    )
        except Exception as error:
            issues.append(f"cannot validate {entry.id}: {type(error).__name__}")
    return issues
