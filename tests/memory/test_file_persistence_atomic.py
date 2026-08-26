from __future__ import annotations

import time
from datetime import datetime, timezone

from kirara_ai.im.sender import ChatSender, ChatType
from kirara_ai.memory.entry import MemoryEntry
from kirara_ai.memory.persistences.base import AsyncMemoryPersistence
from kirara_ai.memory.persistences.file_persistence import FileMemoryPersistence


def _entry(content: str) -> MemoryEntry:
    return MemoryEntry(
        sender=ChatSender(user_id="user", chat_type=ChatType.C2C, display_name="User"),
        content=content,
        timestamp=datetime.now(timezone.utc),
        metadata={},
    )


def test_file_memory_save_replaces_file_atomically_and_round_trips(tmp_path):
    persistence = FileMemoryPersistence(str(tmp_path))

    persistence.save("scope", [_entry("first")])
    persistence.save("scope", [_entry("second")])

    restored = persistence.load("scope")
    assert [item.content for item in restored] == ["second"]
    assert not list(tmp_path.glob("*.tmp"))


def test_async_memory_stop_drains_queued_writes(tmp_path):
    persistence = FileMemoryPersistence(str(tmp_path))
    async_persistence = AsyncMemoryPersistence(persistence)

    for index in range(8):
        async_persistence.save("scope", [_entry(str(index))])

    async_persistence.stop()

    restored = persistence.load("scope")
    assert [item.content for item in restored] == ["7"]
    assert not async_persistence.worker.is_alive()
