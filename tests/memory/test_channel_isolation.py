from datetime import datetime
from unittest.mock import MagicMock

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.memory.entry import MemoryEntry
from kirara_ai.memory.memory_manager import MemoryManager
from kirara_ai.memory.persistences.base import MemoryPersistence
from kirara_ai.memory.scopes import MemberScope


class InMemoryPersistence(MemoryPersistence):
    def __init__(self):
        self.storage = {}

    def load(self, scope_key):
        return list(self.storage.get(scope_key, []))

    def save(self, scope_key, entries):
        self.storage[scope_key] = list(entries)


def test_memory_query_does_not_cross_channel_scopes():
    container = DependencyContainer()
    config = GlobalConfig()
    container.register(GlobalConfig, config)
    container.resolve = MagicMock(return_value=config)
    persistence = InMemoryPersistence()
    manager = MemoryManager(container, persistence=persistence)
    scope = MemberScope()
    sender = ChatSender.from_c2c_chat("same-user", "Researcher")

    manager.store(
        scope,
        MemoryEntry(sender, "telegram-only", datetime.now()),
        extra_identifier="channel:telegram",
    )
    manager.store(
        scope,
        MemoryEntry(sender, "wecom-only", datetime.now()),
        extra_identifier="channel:wecom",
    )

    result = manager.query(scope, sender, extra_identifier="channel:telegram")

    assert [entry.content for entry in result] == ["telegram-only"]
