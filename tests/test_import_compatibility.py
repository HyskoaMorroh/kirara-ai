from __future__ import annotations

import subprocess
import sys


def test_lightweight_agent_import_keeps_im_public_exports_usable():
    script = """
from kirara_ai.agent_runtime import AgentDefinition
from kirara_ai.im import IMManager, IMRegistry

assert AgentDefinition.__name__ == "AgentDefinition"
assert IMManager.__name__ == "IMManager"
assert IMRegistry.__name__ == "IMRegistry"
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_onebot_import_does_not_cycle_through_system_utils():
    script = """
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter

assert OneBotAdapter.__name__ == "OneBotAdapter"
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
