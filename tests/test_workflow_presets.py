"""随包预设工作流的结构校验。

预设 YAML 是数据文件，任何拼写错误（不存在的 block 类型、不存在的端口名）
都不会在导入时报错，只会在用户实际触发该工作流时以运行期异常的形式暴露。
这里在 CI 阶段就把它们全部载入并校验一遍。
"""

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from kirara_ai.workflow.core.block.registry import BlockRegistry
from kirara_ai.workflow.core.workflow.validation import validate_workflow_definition
from kirara_ai.workflow.implementations.blocks import register_system_blocks

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGED_PRESETS_ROOT = PROJECT_ROOT / "kirara_ai" / "workflow" / "presets"
DATA_WORKFLOWS_ROOT = PROJECT_ROOT / "data" / "workflows"

# 画布上节点框的最大宽度与高度余量，与 webui/src/components/workflow/useLayout.ts
# 中的 NODE_MAX_WIDTH 保持一致。坐标间距小于这两个值就可能出现视觉重叠。
NODE_MAX_WIDTH = 360
NODE_MAX_HEIGHT = 360


class _ValidationBlock:
    """给 validate_workflow_definition 用的最小 block 视图。"""

    def __init__(self, name: str, type_name: str, config: dict):
        self.name = name
        self.type_name = type_name
        self.config = config


class _ValidationWire:
    """给 validate_workflow_definition 用的最小 wire 视图。"""

    def __init__(self, source_block: str, source_output: str, target_block: str, target_input: str):
        self.source_block = source_block
        self.source_output = source_output
        self.target_block = target_block
        self.target_input = target_input


def _preset_paths() -> list[Path]:
    paths = sorted(PACKAGED_PRESETS_ROOT.glob("*/*.yaml"))
    paths.extend(sorted(DATA_WORKFLOWS_ROOT.glob("chat/*.yaml")))
    return paths


def _preset_ids() -> list[str]:
    return [str(path.relative_to(PROJECT_ROOT)).replace("\\", "/") for path in _preset_paths()]


@pytest.fixture(scope="module")
def block_registry() -> BlockRegistry:
    registry = BlockRegistry()
    register_system_blocks(registry)
    return registry


def _load_preset(path: Path) -> dict:
    yaml = YAML(typ="safe")
    with path.open(encoding="utf-8") as file:
        return yaml.load(file)


def test_preset_files_are_discovered():
    """防止 glob 写错导致下面的用例一条都不跑。"""
    assert len(_preset_paths()) >= 11


@pytest.mark.parametrize("preset_path", _preset_paths(), ids=_preset_ids())
def test_preset_declares_name_description_and_blocks(preset_path: Path):
    """每个预设都要有中文名、说明和至少一个节点。"""
    data = _load_preset(preset_path)

    assert isinstance(data.get("name"), str) and data["name"].strip()
    assert isinstance(data.get("description"), str) and data["description"].strip()
    assert isinstance(data.get("blocks"), list) and data["blocks"]


@pytest.mark.parametrize("preset_path", _preset_paths(), ids=_preset_ids())
def test_preset_block_types_and_ports_resolve(preset_path: Path, block_registry: BlockRegistry):
    """每个 type 必须能在注册表里找到，每条连线的端口也必须真实存在。"""
    data = _load_preset(preset_path)
    blocks = [
        _ValidationBlock(block["name"], block["type"], block.get("params") or {})
        for block in data["blocks"]
    ]
    wires = []
    for block in data["blocks"]:
        for connection in block.get("connected_to") or []:
            wires.append(
                _ValidationWire(
                    block["name"],
                    connection["mapping"]["from"],
                    connection["target"],
                    connection["mapping"]["to"],
                )
            )

    issues = validate_workflow_definition(blocks, wires, block_registry)
    errors = [issue for issue in issues if issue.severity == "error"]

    assert errors == [], "\n".join(f"{issue.code}: {issue.message}" for issue in errors)


@pytest.mark.parametrize("preset_path", _preset_paths(), ids=_preset_ids())
def test_preset_nodes_do_not_overlap(preset_path: Path):
    """预设里写死的坐标必须互不压叠。

    自动排版只对「没有保存过位置」的节点生效，所以 YAML 里的坐标会原样进入
    画布：一旦重叠，用户打开编辑器看到的就是一堆叠在一起的方框。
    """
    data = _load_preset(preset_path)
    positions = {}
    for block in data["blocks"]:
        position = block.get("position")
        assert position is not None, f"节点 {block['name']} 缺少 position"
        positions[block["name"]] = (position["x"], position["y"])

    names = list(positions)
    overlaps = []
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            first_x, first_y = positions[first]
            second_x, second_y = positions[second]
            if abs(first_x - second_x) < NODE_MAX_WIDTH and abs(first_y - second_y) < NODE_MAX_HEIGHT:
                overlaps.append(f"{first}{positions[first]} 与 {second}{positions[second]} 重叠")

    assert overlaps == [], "\n".join(overlaps)


def test_packaged_presets_are_mirrored_into_the_data_directory():
    """随包预设与 data/workflows 下的副本必须保持同步。"""
    packaged = {path.name for path in PACKAGED_PRESETS_ROOT.glob("chat/*.yaml")}
    extracted = {path.name for path in DATA_WORKFLOWS_ROOT.glob("chat/*.yaml")}

    assert packaged <= extracted
    for file_name in packaged:
        packaged_text = (PACKAGED_PRESETS_ROOT / "chat" / file_name).read_text(encoding="utf-8")
        extracted_text = (DATA_WORKFLOWS_ROOT / "chat" / file_name).read_text(encoding="utf-8")
        assert packaged_text == extracted_text, f"{file_name} 的两份副本内容不一致"


def test_presets_do_not_pin_model_ids_that_users_are_unlikely_to_have():
    """模型槽位留空，由用户从下拉框里手动选择本机已配置的模型。"""
    model_keys = {
        "model_name",
        "fallback_model_1",
        "fallback_model_2",
        "fallback_model_3",
        "fallback_model_4",
    }
    pinned = []
    for path in _preset_paths():
        data = _load_preset(path)
        for block in data["blocks"]:
            params = block.get("params") or {}
            for key in model_keys & set(params):
                if params[key]:
                    pinned.append(f"{path.name}:{block['name']}.{key}={params[key]}")

    assert pinned == [], "\n".join(pinned)


def test_presets_do_not_hardcode_a_frozen_date():
    """提示词里不能写死具体日期，应使用占位符或「当前时间」节点。

    只检查会真正发给模型的 params.text，YAML 注释里提到旧的写死日期（用于
    解释这次改动）是允许的。
    """
    frozen = []
    for path in _preset_paths():
        for block in _load_preset(path)["blocks"]:
            params = block.get("params") or {}
            prompt = params.get("text")
            if not isinstance(prompt, str):
                continue
            if "当前日期时间：20" in prompt or "当前系统时间：20" in prompt:
                frozen.append(f"{path.name}:{block['name']}")

    assert frozen == [], "\n".join(frozen)
