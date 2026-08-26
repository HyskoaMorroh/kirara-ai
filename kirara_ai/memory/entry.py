from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict

from kirara_ai.im.sender import ChatSender


@dataclass
class MemoryEntry:
    """基础记忆条目"""

    sender: ChatSender
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Historical memory files store naive local timestamps. Normalize newer
        # timezone-aware runtime entries to that same representation so mixed
        # files remain sortable after an upgrade.
        if self.timestamp.tzinfo is not None:
            self.timestamp = self.timestamp.astimezone().replace(tzinfo=None)
