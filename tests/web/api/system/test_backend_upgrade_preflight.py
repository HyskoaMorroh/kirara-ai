"""后端升级要先确认这台机器装得上，失败时要说清当前还是哪一版。

发现过程：`perform_update` 的后端分支是 `subprocess.run([sys.executable, "-m",
"pip", "install", backend_file], check=True)`。而本项目的两个虚拟环境里都没有
pip：

    $ .venv/Scripts/python.exe -m pip --version
    No module named pip

uv 建的环境默认不装 pip（`uv venv` 不带 `--seed`），而 `pyproject.toml` 用 uv 锁依赖。
那时这条路径抛 `CalledProcessError`，被 `except Exception` 抓住后返回
`{"status": "error", "message": str(e)}`——那个字符串是
`Command '[...]' returned non-zero exit status 1.`：既不提 pip，也不说该怎么办。
用户看到的是「更新失败」加一串命令行，而真实原因是这台机器没有 pip。

这不是「多一句提示」的问题：下载已经发生（几十 MB），摘要已经校验，
然后在最后一步失败——而失败信息把人引向网络或权限，实际上装什么都不会成功。

**关于回滚**：pip 自己的 `UninstallPathSet` 有 `rollback()`，安装失败时会把旧版本
文件放回去，所以「装失败了」这一路旧版本仍然在。真正缺的是另外两件事，
也就是这组测试锁的：

1. **前置检查**：下载之前就确认 pip 可用，失败信息说清是 pip 缺失以及替代做法。
2. **失败时报出当前版本**：告诉用户「你现在还是 3.3.0b14」，
   这是「要不要重试」与「要不要手动装」的判断依据。而升级成功但运行时坏掉那种
   情况，rollback-on-exception 本来也管不了——它需要的是知道回到哪一版。
"""

from __future__ import annotations

import subprocess

import pytest

from kirara_ai.web.api.system import routes as system_routes

# 复用同目录那份最小应用装配。
from tests.web.api.system.test_update_auto_check_config import (  # noqa: F401
    container,
    test_client,
)
from tests.utils.auth_test_utils import auth_headers, setup_auth_service  # noqa: F401


def _an_update_is_available(monkeypatch) -> list[str]:
    """让路由认为「确实有一个新版本」，并记录下载请求。

    前置检查排在版本解析**之后**（见路由里的理由：没有新版本时该说的是
    「已是最新」，那时装不装得上无关紧要）。因此要触发 pip 这一条，
    必须先让版本检查通过——否则测的是「没有新版本」那条分支。

    返回的列表用于断言「一个字节都没下载」。
    """
    downloads: list[str] = []

    async def _resolve_pypi(*_args, **_kwargs):
        return "9.9.9", "https://example.test/kirara.whl", system_routes.ArtifactDigest(
            sha256="0" * 64
        )

    async def _record_download(url, _directory):
        downloads.append(url)
        return "/tmp/wheel.whl", None

    monkeypatch.setattr(system_routes, "get_installed_version", lambda: "3.3.0b14")
    monkeypatch.setattr(system_routes, "resolve_pypi_release", _resolve_pypi)
    monkeypatch.setattr(system_routes, "download_file", _record_download)
    return downloads


class TestPipPreflight:
    def test_a_missing_pip_is_reported_before_anything_is_downloaded(
        self, test_client, auth_headers, monkeypatch
    ):
        """没有 pip 时不要先下载几十 MB 再失败。

        下载是这条路径里最慢、最占带宽的一步，而它的结果在这台机器上必定用不上。
        """
        downloads = _an_update_is_available(monkeypatch)
        monkeypatch.setattr(system_routes, "backend_installer_available", lambda: False)

        response = test_client.post(
            "/backend-api/api/system/update",
            json={"update_backend": True},
            headers=auth_headers,
        )

        assert response.status_code == 409
        assert downloads == [], "pip 不可用时不该下载任何东西"

    def test_the_failure_names_pip_and_says_what_to_do(
        self, test_client, auth_headers, monkeypatch
    ):
        """报错要指向真实原因与可执行的下一步。

        `Command '[...]' returned non-zero exit status 1.` 满足不了这一条：
        它把人引向网络或权限，而真实原因是这个环境里没有 pip。
        """
        _an_update_is_available(monkeypatch)
        monkeypatch.setattr(system_routes, "backend_installer_available", lambda: False)

        response = test_client.post(
            "/backend-api/api/system/update",
            json={"update_backend": True},
            headers=auth_headers,
        )

        message = response.json()["message"]
        assert "pip" in message.lower()
        # 必须给出替代做法：uv 建的环境本来就没有 pip，这不是配置错误。
        assert "uv" in message.lower() or "手动" in message

    def test_having_no_new_version_is_not_reported_as_a_pip_problem(
        self, test_client, auth_headers, monkeypatch
    ):
        """已是最新时该说「已是最新」，而不是叫用户去装 pip。

        这一条钉住前置检查的**位置**：放在版本解析之前会让每个「已是最新」的
        uv 部署都收到一句「请先安装 pip」，然后用户装完 pip 回来发现无事可做。
        """
        async def _no_newer(*_args, **_kwargs):
            return "0.0.0", "", system_routes.ArtifactDigest()

        monkeypatch.setattr(system_routes, "resolve_pypi_release", _no_newer)
        monkeypatch.setattr(system_routes, "backend_installer_available", lambda: False)

        response = test_client.post(
            "/backend-api/api/system/update",
            json={"update_backend": True},
            headers=auth_headers,
        )

        message = response.json()["message"]
        assert "pip" not in message.lower()
        assert "newer" in message.lower()

    def test_a_webui_only_update_is_not_blocked_by_missing_pip(
        self, test_client, auth_headers, monkeypatch
    ):
        """WebUI 升级不经过 pip，不该被后端的前置检查挡住。

        两个组件可以分别升级；把 pip 检查放在整条路径入口会让「只更新前端」
        在 uv 环境里永久不可用。
        """
        monkeypatch.setattr(system_routes, "backend_installer_available", lambda: False)

        async def _resolve_npm(*_args, **_kwargs):
            # 让 WebUI 分支自己因为「没有更新」而停下，而不是因为 pip。
            return "0.0.0", "", system_routes.ArtifactDigest()

        monkeypatch.setattr(system_routes, "resolve_npm_release", _resolve_npm)

        response = test_client.post(
            "/backend-api/api/system/update",
            json={"update_webui": True},
            headers=auth_headers,
        )

        assert "pip" not in response.json().get("message", "").lower()


class TestInstallFailureReporting:
    def test_the_installed_version_is_reported_when_the_install_fails(
        self, test_client, auth_headers, monkeypatch
    ):
        """失败时要说清「你现在还是哪一版」。

        这是用户判断「重试」还是「手动装」的依据。pip 自己会回滚卸载步骤，
        所以旧版本仍然在——但只有把版本号说出来，用户才知道这件事。
        """
        monkeypatch.setattr(system_routes, "backend_installer_available", lambda: True)
        monkeypatch.setattr(system_routes, "get_installed_version", lambda: "3.3.0b14")

        async def _resolve_pypi(*_args, **_kwargs):
            return "9.9.9", "https://example.test/kirara.whl", system_routes.ArtifactDigest(
                sha256="0" * 64
            )

        async def _download(_url, _directory):
            return "/tmp/kirara.whl", None

        def _boom(*_args, **_kwargs):
            raise subprocess.CalledProcessError(1, ["pip", "install"])

        monkeypatch.setattr(system_routes, "resolve_pypi_release", _resolve_pypi)
        monkeypatch.setattr(system_routes, "download_file", _download)
        monkeypatch.setattr(system_routes, "verify_artifact_digest", lambda *_a: None)
        monkeypatch.setattr(system_routes.subprocess, "run", _boom)

        response = test_client.post(
            "/backend-api/api/system/update",
            json={"update_backend": True},
            headers=auth_headers,
        )

        assert response.status_code == 500
        message = response.json()["message"]
        assert "3.3.0b14" in message, "失败信息必须说明当前仍在运行哪一版"


class TestInstallerProbe:
    """探针问的是「当前解释器能不能 import pip」。

    安装命令里的 `sys.executable` 就是当前进程的解释器，所以这是一个进程内问题。
    起子进程去问会引入超时与子进程失败等额外失败模式，也会与任何替换
    `subprocess.run` 的测试互相干扰——而这条前置检查不该依赖那些。
    """

    def test_the_probe_reports_false_without_pip(self, monkeypatch):
        monkeypatch.setattr(
            system_routes.importlib.util, "find_spec", lambda _name: None
        )

        assert system_routes.backend_installer_available() is False

    def test_the_probe_reports_true_with_pip(self, monkeypatch):
        monkeypatch.setattr(
            system_routes.importlib.util, "find_spec", lambda _name: object()
        )

        assert system_routes.backend_installer_available() is True

    def test_the_probe_asks_about_pip_and_nothing_else(self, monkeypatch):
        asked: list[str] = []

        def _record(name):
            asked.append(name)
            return object()

        monkeypatch.setattr(system_routes.importlib.util, "find_spec", _record)
        system_routes.backend_installer_available()

        assert asked == ["pip"]

    def test_the_probe_survives_a_broken_import_system(self, monkeypatch):
        """探针本身不能抛：它跑在「要不要继续升级」这个判断之前。

        `find_spec` 会在损坏的 `sys.meta_path` 或非法包名上抛 `ValueError`
        与 `ModuleNotFoundError`，那时该返回「不可用」而不是把整个请求打成 500。
        """

        def _boom(_name):
            raise ValueError("broken import system")

        monkeypatch.setattr(system_routes.importlib.util, "find_spec", _boom)

        assert system_routes.backend_installer_available() is False

    def test_the_probe_matches_this_interpreter(self):
        """不打桩跑一次：探针的答案必须与这个解释器的真实情况一致。

        全打桩的探针测试只能证明「函数会读那个返回值」。这一条把它钉在事实上——
        本项目的两个 uv 虚拟环境里都没有 pip，正是这条特性存在的原因。
        """
        import importlib.util as real

        assert system_routes.backend_installer_available() is (
            real.find_spec("pip") is not None
        )
