"""Data-path contracts that a container restart depends on.

Two defects this pins:

1. The WeCom adapter built its media temp directory from ``os.getcwd()`` instead
   of ``DATA_PATH``. Every other component honors ``DATA_PATH``, so under a
   Compose mount whose working directory differs from the data volume the WeCom
   temp files landed outside the mounted volume — invisible to backups and lost
   on every recreate.
2. Startup directory creation used a bare ``os.makedirs`` with no error handling.
   A read-only or full volume produced a raw ``PermissionError`` /
   ``OSError`` at import time with no indication of which path or what to do,
   while the good diagnostics only existed behind the HTTP readiness endpoint —
   unreachable, because the process could not boot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import kirara_ai.config as config_module
from kirara_ai.config import DATA_PATH, ensure_data_directories


def test_wecom_temp_dir_lives_under_the_configured_data_path():
    from kirara_ai.plugins.im_wecom_adapter.adapter import WECOM_TEMP_DIR

    assert Path(WECOM_TEMP_DIR).is_relative_to(Path(DATA_PATH))
    assert Path(WECOM_TEMP_DIR).parts[-2:] == ("temp", "wecom")


def test_ensure_data_directories_creates_missing_directories(tmp_path: Path):
    target = tmp_path / "data" / "nested" / "plugins"

    ensure_data_directories([str(target)])

    assert target.is_dir()


def test_ensure_data_directories_is_idempotent(tmp_path: Path):
    target = tmp_path / "already-there"
    target.mkdir()

    ensure_data_directories([str(target)])

    assert target.is_dir()


def test_ensure_data_directories_reports_the_path_and_a_remediation(tmp_path: Path):
    """失败必须说清「哪个路径」与「下一步做什么」。

    「用文件当父目录」是「这个路径建不出来」的可移植替身，但**平台给的 errno
    不同**：Linux 报 `ENOTDIR`，Windows 报 `ENOENT`（它先解析整条路径，父级不是
    目录时说「找不到路径」）。因此这里断言的是**处置内容**而不是某一个平台的
    错误码分支——早前断言「权限」或「写」在 Linux 上必然失败：那条路径根本不是
    权限问题，而兜底文案恰好只提权限。

    断言「同名文件占用」是两个平台上都成立的那句话，也正是操作者该做的事
    （移走那个文件），而不是去改 ACL。
    """
    blocker = tmp_path / "data"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RuntimeError) as error:
        ensure_data_directories([str(blocker / "plugins")])

    message = str(error.value)
    assert str(blocker) in message
    # 说清下一步动作，而不只是「出错了」。
    assert "同名文件占用" in message
    # 绝不能落到兜底的权限建议上：那会把人指向一个不存在的权限问题。
    assert "请为该路径授予当前用户的写权限" not in message


def test_ensure_data_directories_rejects_a_path_that_exists_as_a_file(tmp_path: Path):
    target = tmp_path / "data"
    target.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError) as error:
        ensure_data_directories([str(target)])

    assert str(target) in str(error.value)


def test_ensure_data_directories_verifies_the_directory_is_writable(tmp_path: Path, monkeypatch):
    target = tmp_path / "data"
    target.mkdir()

    def deny_probe(*_args, **_kwargs):
        raise PermissionError(13, "read-only file system")

    # Simulate a read-only mount: the directory exists (it was mounted in) but
    # nothing can be created inside it, which `makedirs` alone never detects.
    # Only the probe helper is patched — patching os.open globally would break
    # the test runner itself.
    monkeypatch.setattr(config_module.tempfile, "NamedTemporaryFile", deny_probe)

    with pytest.raises(RuntimeError) as error:
        ensure_data_directories([str(target)])

    assert str(target) in str(error.value)
    assert "不可写" in str(error.value)


def test_data_path_and_plugin_path_exist_after_import():
    from kirara_ai.config import PLUGIN_PATH

    assert Path(DATA_PATH).is_dir()
    assert Path(PLUGIN_PATH).is_dir()
