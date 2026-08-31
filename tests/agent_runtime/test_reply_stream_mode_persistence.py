"""Agent 级 `reply_stream_mode` 必须能存下来、也能通过接口改（需求 4）。

`resolve_reply_stream_mode` 的三层优先级是 **Agent 声明 > 渠道默认 > 进程默认**，
`AgentDefinition.reply_stream_mode` 也早就存在且有定义期校验。但：

- `_agent_to_dict` 不含这个字段 → 显式设过的值在 `registry.json` 落盘后消失，
  重载时 dataclass 默认值把它变回 `inherit`；
- `_agent_payload` / `_agent_from_payload` 不含它 → REST 既读不到也写不了。

于是那条「最上面一层」实际只能靠在进程内手工构造 `AgentDefinition` 触达，而且
跨重启不保留。一个只在内存里生效、重启即丢的配置项比没有这个配置项更糟：
运维设过它、看到生效了，重启之后行为悄悄变回去，而界面上没有任何痕迹。

## 判据

1. **落盘要带上它。** 存了就要能读回来，且值逐字相同。
2. **旧注册表要能读。** 早于本字段的 `registry.json` 没有这个键，
   缺省必须是 `inherit`（跟随上层），而不是报错或默认开启。
3. **REST 读写都要有。** 只读不写等于「界面能看见但改不了」，
   只写不读等于「改了看不见」，两者都是半个功能。
4. **非法值要在写入时拒绝。** 存进注册表之后再校验，那份文件就已经坏了。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kirara_ai.agent_runtime.core import AgentDefinition, AgentRegistry


def _registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(tmp_path)


def _registry_file(tmp_path: Path) -> Path:
    return tmp_path / "agents" / "registry.json"


def _agent(agent_id: str = "office", **kwargs) -> AgentDefinition:
    kwargs.setdefault("model_priority", ("openai:gpt-4o",))
    return AgentDefinition(agent_id=agent_id, display_name="办公助手", **kwargs)


class TestPersistence:
    def test_an_explicit_mode_survives_a_reload(self, tmp_path: Path):
        registry = _registry(tmp_path)
        registry.register(_agent(reply_stream_mode="incremental"))

        reloaded = _registry(tmp_path)
        assert reloaded.get("office").reply_stream_mode == "incremental"

    def test_the_field_is_written_to_the_registry_file(self, tmp_path: Path):
        registry = _registry(tmp_path)
        registry.register(_agent(reply_stream_mode="aggregate"))

        payload = json.loads(_registry_file(tmp_path).read_text(encoding="utf-8"))
        stored = next(item for item in payload["agents"] if item["agent_id"] == "office")
        assert stored["reply_stream_mode"] == "aggregate"

    def test_inherit_also_survives(self, tmp_path: Path):
        """默认值也要落盘：缺键与「显式设为 inherit」在读取时无从区分，
        但落盘缺键会让这个字段看起来不存在。"""
        registry = _registry(tmp_path)
        registry.register(_agent())

        payload = json.loads(_registry_file(tmp_path).read_text(encoding="utf-8"))
        stored = next(item for item in payload["agents"] if item["agent_id"] == "office")
        assert stored["reply_stream_mode"] == "inherit"

    def test_a_registry_written_before_this_field_still_loads(self, tmp_path: Path):
        """既有部署升级后行为不变：缺键按 `inherit` 处理。"""
        registry = _registry(tmp_path)
        registry.register(_agent())
        path = _registry_file(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload["agents"]:
            item.pop("reply_stream_mode", None)
        path.write_text(json.dumps(payload), encoding="utf-8")

        reloaded = _registry(tmp_path)
        assert reloaded.get("office").reply_stream_mode == "inherit"

    def test_an_update_can_change_the_mode(self, tmp_path: Path):
        registry = _registry(tmp_path)
        registry.register(_agent(reply_stream_mode="off"))
        registry.update(_agent(reply_stream_mode="incremental"))

        reloaded = _registry(tmp_path)
        assert reloaded.get("office").reply_stream_mode == "incremental"


class TestValidation:
    def test_an_invalid_mode_is_refused_at_definition_time(self):
        with pytest.raises(ValueError):
            _agent(reply_stream_mode="sometimes")

    def test_a_registry_file_with_an_invalid_mode_is_refused(self, tmp_path: Path):
        """坏值不能被静默接受：那会让「三层优先级」在这个 Agent 上永久失效。"""
        registry = _registry(tmp_path)
        registry.register(_agent())
        path = _registry_file(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["agents"][0]["reply_stream_mode"] = "sometimes"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError):
            _registry(tmp_path)

    @pytest.mark.parametrize("mode", ["off", "aggregate", "incremental", "inherit"])
    def test_every_declared_mode_is_accepted(self, mode, tmp_path: Path):
        registry = _registry(tmp_path)
        registry.register(_agent(reply_stream_mode=mode))

        assert _registry(tmp_path).get("office").reply_stream_mode == mode
