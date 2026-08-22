from types import SimpleNamespace
from unittest.mock import MagicMock

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugin_manager.plugin import Plugin
from kirara_ai.plugin_manager.plugin_loader import PluginLoader


def _loader(tmp_path, *, enabled=None):
    container = DependencyContainer()
    config = GlobalConfig()
    config.plugins.enable = list(enabled or [])
    container.register(GlobalConfig, config)
    container.register(EventBus, EventBus())
    return PluginLoader(container, str(tmp_path))


def test_legacy_onebot_name_resolves_to_loaded_internal_plugin(tmp_path):
    loader = _loader(tmp_path)
    internal_plugin = object()
    loader.internal_plugins.append("im_onebot_adapter")
    loader.plugins["im_onebot_adapter"] = internal_plugin
    loader._load_external_plugin = MagicMock()

    loaded = loader.load_plugin("im_onebot_adapters")

    assert loaded is internal_plugin
    loader._load_external_plugin.assert_not_called()


def test_external_legacy_onebot_entry_point_cannot_override_internal_plugin(
    tmp_path, monkeypatch
):
    loader = _loader(tmp_path, enabled=["im_onebot_adapters"])
    loader.internal_plugins.append("im_onebot_adapter")
    loader.plugins["im_onebot_adapter"] = object()
    loader._load_external_plugin = MagicMock()
    entry_point = SimpleNamespace(
        name="im_onebot_adapters", group=Plugin.ENTRY_POINT_GROUP
    )
    distribution = SimpleNamespace(
        entry_points=[entry_point],
        metadata={
            "Name": "chatgpt-mirai-qq-bot-onebot-adapter",
            "Summary": "legacy OneBot adapter",
            "Version": "0.1.0",
            "Author": "legacy",
        },
    )
    monkeypatch.setattr(
        "importlib.metadata.distributions", lambda: [distribution]
    )

    loader.discover_external_plugins()

    assert "im_onebot_adapters" not in loader.plugin_infos
    loader._load_external_plugin.assert_not_called()


def test_real_internal_plugin_directory_discovers_onebot_adapter():
    container = DependencyContainer()
    config = GlobalConfig()
    container.register(GlobalConfig, config)
    container.register(EventBus, EventBus())
    loader = PluginLoader(
        container,
        str(__import__("pathlib").Path(__file__).parents[1] / "kirara_ai" / "plugins"),
    )

    loader.discover_internal_plugins()

    assert "im_onebot_adapter" in loader.internal_plugins
    plugin = loader.plugins["im_onebot_adapter"]
    assert plugin.__class__.__name__ == "OneBotAdapterPlugin"
    assert plugin.__class__.__module__ == "im_onebot_adapter"
