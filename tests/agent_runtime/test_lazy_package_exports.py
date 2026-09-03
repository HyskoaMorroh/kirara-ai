"""`kirara_ai.agent_runtime` 的公开名必须惰性导入，且一个都不能少。

为什么这条必须有测试
------------------
这个包同时是一个**独立命令入口**的父包：内置 `hook:ai-debug` 的五个事件各自起
`python -m kirara_ai.agent_runtime.audit_hook_command <Event>` 子进程，而 `-m`
按 runpy 的规定必须先导入父包。

原来 `__init__.py` 是六行 eager import，于是那个「零依赖」的命令要先把 executor
拉进来，连带 pydantic 与 asyncio。实测（本机，5 次取平均）：

    -m  走包 __init__ : 1.52s
    直接跑该文件      : 0.14s

内置 hook 一轮对话触发五个事件，也就是**每轮多付约 7 秒**——而每个 hook 的
`timeout_ms` 是 5000。这不是测试环境的怪相：生产里每一轮都在付，超时后
`_terminate_process_tree` 杀掉子进程、那个事件记成失败。

改成惰性之后 `-m` 降到 0.10s。但惰性导入有两种典型的坏掉方式，都**没有症状**
直到某个调用点崩掉：

1. 映射表漏了一个名字 —— `from ... import X` 抛 `AttributeError`，
   读起来像「这个 API 被删了」；
2. 有人在别处加回一行 eager import —— 性能悄悄退回去，而所有测试照样绿。

所以这组用例同时钉住**名字齐全**与**真的没被提前导入**。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import kirara_ai.agent_runtime as agent_runtime

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 需要重量级依赖的子模块。它们不该在 `import kirara_ai.agent_runtime` 时被拉进来。
_HEAVY_SUBMODULES = (
    "kirara_ai.agent_runtime.executor",
    "kirara_ai.agent_runtime.hooks",
    "kirara_ai.agent_runtime.core",
    "kirara_ai.agent_runtime.session_store",
)


def _in_clean_subprocess(code: str) -> str:
    """在干净子进程里执行一段代码并返回 stdout。

    必须用子进程：当前测试进程早已把这些子模块全都导入过，
    在进程内问「它被导入了吗」只会得到「是」，与被测行为无关。
    """
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"子进程失败（exit={result.returncode}）\n"
            f"--- stderr ---\n{result.stderr[-2000:]}\n"
            f"--- stdout ---\n{result.stdout[-2000:]}"
        )
    return result.stdout.strip()


class TestEveryPublicNameStillResolves:
    """`__all__` 里的每一个名字都要能取到。漏一个就是一次 `AttributeError`。"""

    @pytest.mark.parametrize("name", sorted(agent_runtime.__all__))
    def test_the_name_resolves(self, name: str):
        assert getattr(agent_runtime, name) is not None

    def test_the_lazy_table_covers_all_of_dunder_all(self):
        """映射表与 `__all__` 必须逐项对齐。

        分开写这一条是因为上面那个参数化用例只能证明「现在取得到」；
        这一条证明**没有名字是靠别处的副作用才存在的**——
        某个子模块被别的 import 拉进来后，`getattr` 也会成功。
        """
        assert set(agent_runtime._LAZY_EXPORTS) == set(agent_runtime.__all__)

    def test_an_unknown_name_raises_attribute_error(self):
        # 不是抛 KeyError，也不是返回 None：调用方按 AttributeError 处理缺失属性。
        with pytest.raises(AttributeError):
            agent_runtime.ThisNameDoesNotExist  # noqa: B018

    def test_dir_still_lists_the_public_names(self):
        """惰性导出不能让 `dir()` 变空——那会让人以为 API 被删了。"""
        assert set(agent_runtime.__all__) <= set(dir(agent_runtime))


class TestNothingHeavyIsImportedEagerly:
    def test_importing_the_package_pulls_in_no_submodule(self):
        """`import kirara_ai.agent_runtime` 之后，四个子模块都不该在 `sys.modules` 里。

        这是「惰性」的直接判据。有人加回一行 eager import 时这条会红，
        而所有功能测试都不会——那正是这条护栏存在的理由。
        """
        code = (
            "import json, sys;"
            "import kirara_ai.agent_runtime;"
            "print(json.dumps(sorted("
            "k for k in sys.modules if k.startswith('kirara_ai.agent_runtime')"
            ")))"
        )
        import json

        loaded = json.loads(_in_clean_subprocess(code))
        assert loaded == ["kirara_ai.agent_runtime"], (
            f"导入包时把这些子模块一起拉进来了：{loaded}"
        )

    @pytest.mark.parametrize("submodule", _HEAVY_SUBMODULES)
    def test_the_submodule_is_absent_until_a_name_is_touched(self, submodule: str):
        code = (
            "import sys;"
            "import kirara_ai.agent_runtime;"
            f"print({submodule!r} in sys.modules)"
        )
        assert _in_clean_subprocess(code) == "False"

    def test_touching_a_name_does_import_its_submodule(self):
        """惰性不等于不导入：取用之后必须真的加载。

        少了这一条，一个「永远返回 None」的假实现也能通过上面几条。
        """
        code = (
            "import sys;"
            "import kirara_ai.agent_runtime as m;"
            "m.AgentRuntimeExecutor;"
            "print('kirara_ai.agent_runtime.executor' in sys.modules)"
        )
        assert _in_clean_subprocess(code) == "True"


class TestTheHookCommandStaysCheap:
    """内置 hook 的命令入口不能因为父包而变重。"""

    def test_the_command_entry_point_does_not_load_the_executor(self):
        """`audit_hook_command` 自称零依赖，那就不该拉起 executor。

        它是这条优化的**受益者**：`-m` 先导入父包，父包 eager 时这个命令
        每次多付 1.38s，而内置 hook 一轮触发五次。
        """
        code = (
            "import sys;"
            "import kirara_ai.agent_runtime.audit_hook_command;"
            "print('kirara_ai.agent_runtime.executor' in sys.modules)"
        )
        assert _in_clean_subprocess(code) == "False"

    def test_pydantic_is_not_pulled_in_by_the_command(self):
        """pydantic 是这 1.38s 里最大的一块。"""
        code = (
            "import sys;"
            "import kirara_ai.agent_runtime.audit_hook_command;"
            "print('pydantic' in sys.modules)"
        )
        assert _in_clean_subprocess(code) == "False"

    def test_the_command_still_answers_the_protocol(self):
        """跑一次真实调用：入口变轻了，协议不能跟着变。

        只测「没导入什么」会让一个空实现也通过——那时 hook 全部静默失败。
        """
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kirara_ai.agent_runtime.audit_hook_command",
                "SessionStart",
            ],
            cwd=PROJECT_ROOT,
            input=b"{}",
            capture_output=True,
            timeout=120,
            check=False,
        )

        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        import json

        payload = json.loads(result.stdout.decode("utf-8"))
        assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    def test_an_invalid_event_is_still_rejected(self):
        """轻量化不能顺手放宽校验。"""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kirara_ai.agent_runtime.audit_hook_command",
                "NotAnEvent",
            ],
            cwd=PROJECT_ROOT,
            input=b"{}",
            capture_output=True,
            timeout=120,
            check=False,
        )

        assert result.returncode != 0
