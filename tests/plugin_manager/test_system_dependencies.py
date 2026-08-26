from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from kirara_ai.plugin_manager.system_dependencies import (
    CommandResult,
    DependencyInstallConfirmationRequired,
    DependencyInstallUnsupported,
    DependencyNotFoundError,
    SystemDependencyService,
    _resolve_command_argv,
)


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.installed: set[str] = set()
        self.fail_install = False
        self.timeout = False

    def __call__(self, argv, *, timeout, cancellation_event, output_sink):
        command = tuple(argv)
        self.calls.append(command)
        if command == ("node", "--version"):
            return CommandResult(0, "v24.19.0")
        if command == ("npm", "--version"):
            return CommandResult(0, "11.11.0")
        if command == ("npx", "--version"):
            return CommandResult(0, "11.11.0")
        if command == ("agent-browser", "--version"):
            if "agent-browser-cli" in self.installed:
                return CommandResult(0, "agent-browser 0.34.0")
            return CommandResult(1, "agent-browser: command not found")
        if command == ("npm", "install", "-g", "agent-browser"):
            if self.timeout:
                return CommandResult(exit_code=None, output="command timed out", timed_out=True)
            if self.fail_install:
                return CommandResult(1, "token=secret-value C:\\Users\\devin\\AppData\\Roaming\\npm")
            self.installed.add("agent-browser-cli")
            return CommandResult(0, "installed agent-browser")
        if command == ("agent-browser", "doctor", "--offline", "--quick", "--json"):
            if "agent-browser-browser" in self.installed:
                return CommandResult(0, '{"browser":"ready"}')
            return CommandResult(1, '{"browser":"missing"}')
        if command == ("agent-browser", "install"):
            self.installed.add("agent-browser-browser")
            return CommandResult(0, "browser installed")
        raise AssertionError(f"unexpected command: {command}")


def _service(tmp_path: Path, runner: FakeRunner) -> SystemDependencyService:
    return SystemDependencyService(tmp_path / "data", command_runner=runner)


def test_public_dependency_records_do_not_expose_server_commands(tmp_path: Path):
    service = _service(tmp_path, FakeRunner())

    records = service.list_dependencies()

    browser = next(item for item in records if item["dependency_id"] == "agent-browser-cli")
    assert browser["install_supported"] is True
    assert "install_argv" not in browser
    assert "probe_argv" not in browser
    assert browser["status"] == "unknown"


def test_fixed_command_is_resolved_through_server_path(monkeypatch):
    resolved = str(Path("server-tools") / ("npm.cmd" if os.name == "nt" else "npm"))
    monkeypatch.setattr(
        "kirara_ai.plugin_manager.system_dependencies.shutil.which",
        lambda command, path=None: resolved if command == "npm" else None,
    )

    command = _resolve_command_argv(("npm", "--version"), {"PATH": "server-tools"})

    assert command == [resolved, "--version"]


def test_probe_does_not_duplicate_streamed_command_output(tmp_path: Path):
    def streaming_runner(argv, *, timeout, cancellation_event, output_sink):
        assert tuple(argv) == ("graphify", "--version")
        output_sink("graphify 1.0\n")
        return CommandResult(0, "graphify 1.0\n")

    service = SystemDependencyService(tmp_path / "data", command_runner=streaming_runner)

    result = service.probe("graphify-cli")

    assert result["ready"] is True
    assert result["summary"] == "graphify 1.0"


def test_browser_probe_exposes_only_a_safe_summary(tmp_path: Path):
    raw = json.dumps(
        {
            "checks": [
                {
                    "id": "chrome.installed",
                    "message": "152.0.7977.54 at C:\\private\\browser",
                    "status": "pass",
                },
                {
                    "id": "security.encryption_key",
                    "message": "AGENT_BROWSER_ENCRYPTION_KEY not set",
                    "fix": "export AGENT_BROWSER_ENCRYPTION_KEY=private-value",
                    "status": "info",
                },
            ],
            "success": True,
            "summary": {"pass": 6, "warn": 0, "fail": 0},
        }
    )

    def browser_runner(argv, *, timeout, cancellation_event, output_sink):
        assert tuple(argv) == ("agent-browser", "doctor", "--offline", "--quick", "--json")
        output_sink(raw)
        return CommandResult(0, raw)

    service = SystemDependencyService(tmp_path / "data", command_runner=browser_runner)

    result = service.probe("agent-browser-browser")

    assert result["ready"] is True
    assert result["version"] == "152.0.7977.54"
    assert result["summary"] == "Agent Browser browser checks completed: 6 passed, 0 warnings, 0 failed"
    assert "private" not in json.dumps(result)
    assert "AGENT_BROWSER_ENCRYPTION_KEY" not in json.dumps(result)
    assert "export" not in json.dumps(result)


def test_unknown_dependency_and_client_command_injection_are_rejected(
    tmp_path: Path,
):
    service = _service(tmp_path, FakeRunner())

    with pytest.raises(DependencyNotFoundError):
        service.get_dependency("npm install --global attacker-package")
    with pytest.raises(DependencyNotFoundError):
        service.install("agent-browser-cli; whoami", confirmed=True)


def test_install_requires_explicit_confirmation(tmp_path: Path):
    service = _service(tmp_path, FakeRunner())

    with pytest.raises(DependencyInstallConfirmationRequired):
        service.install("agent-browser-cli", confirmed=False, start=False)


def test_install_uses_fixed_argv_rechecks_readiness_and_persists_task(
    tmp_path: Path,
):
    runner = FakeRunner()
    runner.installed.update({"node-runtime", "npm-runtime"})
    service = _service(tmp_path, runner)

    task = service.install("agent-browser-cli", confirmed=True, start=False)
    assert task["status"] == "queued"
    completed = service.run_task(task["task_id"])

    assert completed["status"] == "succeeded"
    assert ("npm", "install", "-g", "agent-browser") in runner.calls
    assert all(";" not in argument and "&&" not in argument for call in runner.calls for argument in call)
    assert service.get_dependency("agent-browser-cli")["ready"] is True

    reloaded = _service(tmp_path, runner)
    assert reloaded.get_task(task["task_id"])["status"] == "succeeded"
    assert json.loads((tmp_path / "data" / "dependencies" / "registry.json").read_text())["dependencies"]


def test_failed_install_redacts_output_and_retry_links_to_original_task(tmp_path: Path):
    runner = FakeRunner()
    runner.installed.update({"node-runtime", "npm-runtime"})
    runner.fail_install = True
    service = _service(tmp_path, runner)

    first = service.install("agent-browser-cli", confirmed=True, start=False)
    failed = service.run_task(first["task_id"])
    assert failed["status"] == "failed"
    assert "secret-value" not in json.dumps(failed)
    assert "C:\\Users\\devin" not in json.dumps(failed)

    runner.fail_install = False
    retry = service.retry_task(first["task_id"], confirmed=True, start=False)
    assert retry["retry_of"] == first["task_id"]
    assert retry["status"] == "queued"
    assert service.run_task(retry["task_id"])["status"] == "succeeded"


def test_timeout_is_a_failed_task_and_does_not_mark_dependency_ready(tmp_path: Path):
    runner = FakeRunner()
    runner.installed.update({"node-runtime", "npm-runtime"})
    runner.timeout = True
    service = _service(tmp_path, runner)

    task = service.install("agent-browser-cli", confirmed=True, start=False)
    result = service.run_task(task["task_id"])

    assert result["status"] == "failed"
    assert result["error_code"] == "timeout"
    assert service.get_dependency("agent-browser-cli")["ready"] is False


def test_queued_task_can_be_cancelled_and_cancel_is_audited(tmp_path: Path):
    service = _service(tmp_path, FakeRunner())

    task = service.install("agent-browser-cli", confirmed=True, start=False)
    cancelled = service.cancel_task(task["task_id"])

    assert cancelled["status"] == "cancelled"
    audit = (tmp_path / "data" / "dependencies" / "audit.jsonl").read_text()
    assert "cancel_requested" in audit


def test_running_task_is_observable_and_can_be_cancelled(tmp_path: Path):
    entered = threading.Event()
    release = threading.Event()

    def blocking_runner(argv, *, timeout, cancellation_event, output_sink):
        if tuple(argv) == ("agent-browser", "--version"):
            return CommandResult(1, "agent-browser: command not found")
        if tuple(argv) == ("node", "--version"):
            return CommandResult(0, "v24.19.0")
        if tuple(argv) == ("npm", "--version"):
            return CommandResult(0, "11.11.0")
        if tuple(argv) == ("npx", "--version"):
            return CommandResult(0, "11.11.0")
        if tuple(argv) == ("npm", "install", "-g", "agent-browser"):
            entered.set()
            while not release.wait(0.01):
                if cancellation_event.is_set():
                    return CommandResult(None, "cancelled", cancelled=True)
            return CommandResult(0, "installed")
        raise AssertionError(f"unexpected command: {argv}")

    service = _service(tmp_path, blocking_runner)
    task = service.install("agent-browser-cli", confirmed=True, start=True)
    assert entered.wait(2)
    for _ in range(200):
        if service.get_task(task["task_id"])["status"] == "running":
            break
        time.sleep(0.01)
    assert service.get_task(task["task_id"])["status"] == "running"

    cancellation = service.cancel_task(task["task_id"])
    assert cancellation["status"] == "running"
    for _ in range(200):
        if service.get_task(task["task_id"])["status"] == "cancelled":
            break
        time.sleep(0.01)
    release.set()
    assert service.get_task(task["task_id"])["status"] == "cancelled"
    assert service.get_dependency("agent-browser-cli")["ready"] is False


def test_service_restart_recovers_queued_and_running_tasks_as_failed(tmp_path: Path):
    service = _service(tmp_path, FakeRunner())
    queued = service.install("agent-browser-cli", confirmed=True, start=False)
    running = service.install("agent-browser-cli", confirmed=True, start=False)
    payload = json.loads(service.tasks_path.read_text(encoding="utf-8"))
    for item in payload["tasks"]:
        item["status"] = "running" if item["task_id"] == running["task_id"] else "queued"
    service.tasks_path.write_text(json.dumps(payload), encoding="utf-8")

    recovered = _service(tmp_path, FakeRunner())

    assert recovered.get_task(queued["task_id"])["status"] == "failed"
    assert recovered.get_task(running["task_id"])["status"] == "failed"
    assert recovered.get_task(queued["task_id"])["error_code"] == "service_restarted"
    assert recovered.get_task(running["task_id"])["error_code"] == "service_restarted"


def test_dependency_without_server_owned_installer_requires_vps_operator(tmp_path: Path):
    service = _service(tmp_path, FakeRunner())

    dependency = service.get_dependency("python-tooling")
    assert dependency["install_supported"] is False
    assert dependency["operator_guidance"]
    with pytest.raises(DependencyInstallUnsupported):
        service.install("python-tooling", confirmed=True, start=False)
