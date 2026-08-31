"""容器必须真的收到 SIGTERM，否则所有优雅关闭逻辑都不执行（需求 18.3）。

`entry.py` 注册了 `SIGTERM` 处理器，`finally` 块里做了完整的收尾：停调度器、
flush 记忆（异步 daemon 线程，只有走完这一步才保证落盘）、关追踪与数据库、
停 Web 服务器、停所有适配器、断开 MCP。18.3 要求「连接恢复后的状态同步」与
「不会重复发送同一消息的幂等或去重策略」，而出站队列的 `sending → ambiguous`
隔离正是靠这段收尾与启动恢复配合完成的。

但 `docker/start.sh` 的最后一行是 `python -m kirara_ai`——**没有 `exec`**。
于是容器里 PID 1 是 bash，bash 没有 `trap`，`docker stop` 发来的 SIGTERM 被它
吞掉（bash 在等子进程时不转发信号），10 秒宽限期后整个容器被 SIGKILL。
结果：那段 `finally` **一次都不会跑**。

三个可观察后果：

- 记忆的异步写队列没 flush，最后几条对话记忆丢失；
- 出站队列里 `sending` 状态的投递不会被隔离成 `ambiguous`，
  下次启动的 `recover_on_startup()` 面对的是一份不完整的现场；
- 适配器不走 `stop()`，反向 WebSocket 不做有序断开，上游看到的是连接被硬切。

`docker compose down` 正是走这条路，而需求 1 与 18 的整个「重启恢复」都建立在
「上一次是干净停下的」这个前提上。

## 判据

用 `exec` 把 Python 换成 PID 1。这是容器里跑单进程的标准做法，代价为零：
bash 到这一行已经无事可做，它继续存在只是为了挡住信号。

顺带钉住 compose 的宽限期：默认 10 秒对「flush 记忆 + 关数据库 + 停 N 个适配器」
偏紧，超时就退回 SIGKILL，等于 `exec` 白加。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
START_SH = ROOT / "docker" / "start.sh"
DOCKERFILE = ROOT / "Dockerfile"


def _start_script() -> str:
    return START_SH.read_text(encoding="utf-8")


class TestPythonBecomesPidOne:
    def test_the_interpreter_is_exec_ed(self):
        """没有 exec 时 PID 1 是 bash，它会吞掉 SIGTERM。"""
        assert re.search(r"^\s*exec\s+python\s+-m\s+kirara_ai", _start_script(), re.MULTILINE), (
            "start.sh 最后一行没有 exec，SIGTERM 到不了 Python"
        )

    def test_there_is_exactly_one_interpreter_launch(self):
        """两处启动会让 exec 那一处形同虚设——先执行的那一行决定了 PID 1。"""
        launches = re.findall(r"python\s+-m\s+kirara_ai", _start_script())

        assert len(launches) == 1

    def test_the_venv_is_still_activated_before_it(self):
        """`exec` 会替换进程映像，因此激活必须在它之前。"""
        script = _start_script()
        activate = script.index("source /app/data/venv/bin/activate")
        launch = script.index("exec python -m kirara_ai")

        assert activate < launch


class TestTheDockerfileDoesNotReintroduceAWrapper:
    def test_the_cmd_runs_the_script(self):
        content = DOCKERFILE.read_text(encoding="utf-8")

        assert "/app/docker/start.sh" in content

    def test_no_stopsignal_override_defeats_sigterm(self):
        """改成别的信号会绕过 `entry.py` 注册的那两个处理器。"""
        content = DOCKERFILE.read_text(encoding="utf-8")
        match = re.search(r"^STOPSIGNAL\s+(\S+)", content, re.MULTILINE)

        assert match is None or match.group(1) in {"SIGTERM", "SIGINT"}


class TestComposeAllowsTimeToShutDown:
    @pytest.mark.parametrize(
        "compose_name", ["docker-compose.yml", "docker-compose.yml.example"]
    )
    def test_the_grace_period_is_long_enough(self, compose_name):
        """默认 10 秒对「flush 记忆 + 关库 + 停 N 个适配器」偏紧。

        超时就退回 SIGKILL，那样 `exec` 白加——信号到了，但没时间用。
        """
        raw = (ROOT / compose_name).read_text(encoding="utf-8")
        # `${VAR:?msg}` 不是合法 YAML 标量里的插值，先替换掉再解析。
        raw = re.sub(r"\$\{[^}]*\}", "placeholder", raw)
        compose = yaml.safe_load(raw)
        service = compose["services"]["kirara-agent"]

        assert "stop_grace_period" in service, (
            f"{compose_name} 没有声明 stop_grace_period，10 秒后会被 SIGKILL"
        )
        value = str(service["stop_grace_period"])
        seconds = int(re.sub(r"[^0-9]", "", value) or 0)
        if value.endswith("m"):
            seconds *= 60
        assert seconds >= 30, f"{compose_name} 的宽限期只有 {value}"
