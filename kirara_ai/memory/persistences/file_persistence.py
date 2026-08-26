import json
import os
import tempfile
from datetime import datetime
from typing import List

from kirara_ai.memory.entry import MemoryEntry

from .base import MemoryPersistence
from .codecs import MemoryJSONEncoder, memory_json_decoder


class FileMemoryPersistence(MemoryPersistence):
    """文件持久化实现"""

    def __init__(self, data_dir: str):
        if not os.path.isabs(data_dir):
            data_dir = os.path.abspath(data_dir)

        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

    def _get_file_path(self, scope_key: str) -> str:
        scope_key = scope_key.replace(":", "_")
        return os.path.join(self.data_dir, f"{scope_key}.json")

    def save(self, scope_key: str, entries: List[MemoryEntry]) -> None:
        file_path = self._get_file_path(scope_key)

        # 序列化记忆条目
        serialized_entries = [
            {
                "sender": entry.sender,
                "content": entry.content,
                "timestamp": entry.timestamp,
                "metadata": entry.metadata,
            }
            for entry in entries
        ]

        # Write to a sibling temporary file and replace the target so a
        # process stop cannot leave a truncated memory document behind.
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{os.path.basename(file_path)}-",
            suffix=".tmp",
            dir=self.data_dir,
        )
        temporary_path = temporary_name
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as f:
                json.dump(
                    serialized_entries,
                    f,
                    ensure_ascii=False,
                    indent=2,
                    cls=MemoryJSONEncoder,
                )
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary_path, file_path)
        finally:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

    def load(self, scope_key: str) -> List[MemoryEntry]:
        file_path = self._get_file_path(scope_key)

        if not os.path.exists(file_path):
            return []

        # 读取并反序列化
        with open(file_path, "r", encoding="utf-8") as f:
            serialized_entries = json.load(f, object_hook=memory_json_decoder)

        return [
            MemoryEntry(
                sender=entry["sender"],
                content=entry["content"],
                timestamp=(
                    datetime.fromisoformat(entry["timestamp"])
                    if isinstance(entry["timestamp"], str)
                    else entry["timestamp"]
                ),
                metadata=entry["metadata"],
            )
            for entry in serialized_entries
        ]

    def flush(self) -> None:
        # Each save is fsynced before replace; no additional buffer remains.
        pass
