from unittest.mock import mock_open, patch

import pytest

from kirara_ai.web.api.system import utils


@pytest.fixture(autouse=True)
def clear_cpu_info_cache():
    utils.get_cpu_info.cache_clear()
    yield
    utils.get_cpu_info.cache_clear()


def test_get_cpu_info_reads_linux_hardware_field():
    cpuinfo = "processor\t: 0\nHardware\t: Ampere Altra Max M128-30\n"

    with patch.object(utils.sys, "platform", "linux"), patch(
        "builtins.open", mock_open(read_data=cpuinfo)
    ):
        assert utils.get_cpu_info() == "Ampere Altra Max M128-30"


def test_get_cpu_info_falls_back_to_lscpu_when_proc_is_unavailable():
    lscpu = "Architecture: x86_64\nModel name: AMD EPYC 7B13 64-Core Processor\n"

    with patch.object(utils.sys, "platform", "linux"), patch(
        "builtins.open", side_effect=PermissionError
    ), patch.object(
        utils.subprocess,
        "run",
        return_value=utils.subprocess.CompletedProcess(
            args=["lscpu"], returncode=0, stdout=lscpu, stderr=""
        ),
    ) as run:
        assert utils.get_cpu_info() == "AMD EPYC 7B13 64-Core Processor"

    run.assert_called_once()
    assert run.call_args.args[0] == ["lscpu"]
    assert run.call_args.kwargs["shell"] is False
    assert run.call_args.kwargs["timeout"] == 2


def test_get_cpu_info_uses_architecture_as_last_resort():
    with patch.object(utils.sys, "platform", "linux"), patch(
        "builtins.open", mock_open(read_data="processor: 0\n")
    ), patch.object(utils.subprocess, "run", side_effect=FileNotFoundError), patch.object(
        utils, "platform"
    ) as platform_info:
        platform_info.processor.return_value = ""
        platform_info.machine.return_value = "aarch64"

        assert utils.get_cpu_info() == "aarch64"


def test_get_cpu_info_preserves_windows_wmic_support():
    with patch.object(utils.sys, "platform", "win32"), patch.object(
        utils.subprocess,
        "run",
        return_value=utils.subprocess.CompletedProcess(
            args=["wmic", "cpu", "get", "name"],
            returncode=0,
            stdout="Name\nIntel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz\n",
            stderr="",
        ),
    ):
        assert utils.get_cpu_info() == "Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz"
