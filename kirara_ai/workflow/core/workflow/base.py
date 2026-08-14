from typing import List, Optional

from pydantic import BaseModel

from kirara_ai.workflow.core.block import Block


class WorkflowConfig(BaseModel):
    max_execution_time: int = 3600

class Workflow:
    def __init__(self, name: str, blocks: List["Block"], wires: List["Wire"], id: Optional[str] = None, config: Optional[WorkflowConfig] = None):
        self.name = name
        self.blocks = blocks
        self.wires = wires
        self.id = id
        self.config = config or WorkflowConfig()


class Wire:
    def __init__(
        self,
        source_block: "Block",
        source_output: str,
        target_block: "Block",
        target_input: str,
    ):
        self.source_block = source_block
        self.source_output = source_output
        self.target_block = target_block
        self.target_input = target_input

    def __repr__(self):
        return f"Wire(source_block={self.source_block.name}, source_output={self.source_output}, target_block={self.target_block.name}, target_input={self.target_input})"
