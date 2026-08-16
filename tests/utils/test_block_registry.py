from typing import Annotated
from unittest.mock import Mock

from kirara_ai.workflow.core.block import Block, ParamMeta
from kirara_ai.workflow.core.block.registry import BlockRegistry


def create_test_block_registry() -> BlockRegistry:
    """创建一个用于测试的 BlockRegistry 实例"""
    registry = BlockRegistry()

    # 注册一些基本类型
    registry._type_system.register_type("str", str)
    registry._type_system.register_type("int", int)
    registry._type_system.register_type("float", float)
    registry._type_system.register_type("bool", bool)
    registry._type_system.register_type("list", list)
    registry._type_system.register_type("dict", dict)
    registry._type_system.register_type("Any", object)

    return registry


class CachedMetadataBlock(Block):
    """用于验证注册表静态元数据缓存的测试节点。"""

    name = "cached_metadata"

    def __init__(
        self,
        greeting: Annotated[str, ParamMeta(label="问候语")] = "你好",
    ):
        super().__init__()
        self.greeting = greeting


def test_extract_block_info_cached_reuses_reflection_without_sharing_response_state():
    registry = create_test_block_registry()
    extract_block_info = Mock(wraps=registry.extract_block_info)
    registry.extract_block_info = extract_block_info

    _, _, first_configs = registry.extract_block_info_cached(CachedMetadataBlock)
    first_configs["greeting"].label = "已修改"
    _, _, second_configs = registry.extract_block_info_cached(CachedMetadataBlock)

    assert extract_block_info.call_count == 1
    assert second_configs["greeting"].label == "问候语"

    registry.clear()
    registry.extract_block_info_cached(CachedMetadataBlock)
    assert extract_block_info.call_count == 2
