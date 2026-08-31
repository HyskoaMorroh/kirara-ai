"""依赖就绪探针必须能识别同名的**不同**工具。

`rtk-cli` 的描述自己就写着：「注意与同名的 Rust Type Kit 不是同一个工具：
以 `rtk gain` 是否可用为准」。但探针只跑 `rtk --version`——那条命令另一个
`rtk` 也能通过。于是在 VPS 上装错了 crate 时，前端照样显示「就绪」，
之后每一次终端输出压缩都会静默走偏。

判据写在描述里、却没写进探针，是这一类缺陷的典型形态：文档正确、
实现没跟上，而失败是无声的。
"""

from __future__ import annotations

from pathlib import Path

from kirara_ai.plugin_manager.system_dependencies import (CommandResult,
                                                          SystemDependencyService,
                                                          _definitions)


class ProbeRunner:
    """按 argv 精确匹配的假 runner：只有登记过的命令返回 exit 0。"""

    def __init__(self, allowed: set[tuple[str, ...]]) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, *, timeout, cancellation_event, output_sink):
        command = tuple(argv)
        self.calls.append(command)
        if command in self.allowed:
            return CommandResult(exit_code=0, output="rtk 0.45.0", timed_out=False, cancelled=False)
        # 未登记的命令按「可执行文件存在但这条子命令不支持」处理，
        # 这正是装错同名工具时的表现。
        return CommandResult(
            exit_code=1,
            output="error: unrecognized subcommand",
            timed_out=False,
            cancelled=False,
        )


def _rtk_definition():
    definitions = {item.dependency_id: item for item in _definitions()}
    return definitions["rtk-cli"]


def test_the_rtk_probe_uses_the_documented_discriminator():
    """探针必须包含 `rtk gain`——描述里点名的那个判据。"""
    probes = _rtk_definition().probe_commands

    assert ("rtk", "gain") in probes, probes


def test_a_confusable_tool_that_only_answers_version_is_not_ready(tmp_path: Path):
    """只认 `--version` 的同名工具不能被判成就绪。"""
    runner = ProbeRunner({("rtk", "--version")})
    service = SystemDependencyService(tmp_path / "data", command_runner=runner)

    record = service.probe("rtk-cli")

    assert record["ready"] is False
    assert record["status"] == "missing"
    assert ("rtk", "gain") in runner.calls


def test_the_real_rtk_is_ready(tmp_path: Path):
    """两条命令都通过时才算就绪。"""
    runner = ProbeRunner({("rtk", "--version"), ("rtk", "gain")})
    service = SystemDependencyService(tmp_path / "data", command_runner=runner)

    record = service.probe("rtk-cli")

    assert record["ready"] is True


def test_the_probe_never_leaks_server_commands_to_the_client(tmp_path: Path):
    """公开记录里不能出现 argv——这条约束对新增的探针命令同样成立。"""
    runner = ProbeRunner({("rtk", "--version"), ("rtk", "gain")})
    service = SystemDependencyService(tmp_path / "data", command_runner=runner)

    record = service.probe("rtk-cli")

    assert "probe_commands" not in record
    assert "install_commands" not in record
