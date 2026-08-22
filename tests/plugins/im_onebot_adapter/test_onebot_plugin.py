from unittest.mock import MagicMock

from kirara_ai.im.im_registry import IMRegistry
from kirara_ai.plugins.im_onebot_adapter import OneBotAdapterPlugin
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig


def test_onebot_plugin_registers_and_only_unregisters_its_adapter():
    registry = IMRegistry()
    registry._registry = {}
    registry.register("existing", OneBotAdapter, OneBotConfig)
    web_server = MagicMock()
    plugin = OneBotAdapterPlugin()
    plugin.im_registry = registry
    plugin.web_server = web_server

    plugin.on_load()

    info = registry.get_all_adapters()["onebot"]
    assert info.adapter_class is OneBotAdapter
    assert info.config_class is OneBotConfig
    web_server.add_static_assets.assert_called_once()

    plugin.on_stop()

    assert "onebot" not in registry.get_all_adapters()
    assert "existing" in registry.get_all_adapters()
