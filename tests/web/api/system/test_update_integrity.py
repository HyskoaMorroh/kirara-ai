"""升级包必须校验哈希（需求 16）。

`download_file` 一直在算下载内容的 SHA-256 并把它返回，但**没有任何调用点比对它**。
两侧的期望值其实都拿得到：

* PyPI 的 PEP 691 Simple API 在每个文件条目里给 `hashes.sha256`；
* npm registry 在 `dist.shasum`（SHA-1）与 `dist.integrity`（SRI，含 sha512）里给。

丢掉比对的后果不是「少一道校验」，而是**镜像源成了任意代码执行的入口**：
镜像地址是用户可配的（`config.update.pypi_registry` / `npm_registry`），
一个被投毒或被中间人替换的镜像可以返回任意 wheel，而 `perform_update`
会直接把它 `pip install` 掉。此前唯一的保护是 TLS——它只能证明「确实来自这个
镜像」，证明不了「这个镜像给的东西没被换过」。

算了却不比对是最坏的一种形态：代码看起来做了校验（有 `hashlib`、有摘要返回值），
审阅时容易一眼扫过去认为已经校验过了。

三条边界：

* **期望值缺失时不放行。** 拿不到 registry 声明的哈希，就不能装——
  「没人告诉我该是什么」不等于「它是对的」。这一条是本文件里最重要的断言：
  把缺失当通过，等于给攻击者一个「只要别声明哈希」的绕过口。
* **比对不区分大小写、忽略前后空白。** registry 给的十六进制串大小写不统一。
* **不匹配时删掉下载文件。** 留着一个已知被篡改的包在临时目录里，
  下一次「重试升级」可能直接拿它。
"""

from __future__ import annotations

import hashlib

import pytest

from kirara_ai.web.api.system.utils import (
    ArtifactDigest,
    verify_artifact_digest,
)
# 复用同目录那份最小应用装配：本文件关心的是校验，不是容器怎么搭。
from tests.web.api.system.test_update_auto_check_config import (  # noqa: F401
    container,
    test_client,
)
from tests.utils.auth_test_utils import auth_headers, setup_auth_service  # noqa: F401

PAYLOAD = b"pretend this is a wheel"
GOOD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
GOOD_SHA1 = hashlib.sha1(PAYLOAD).hexdigest()  # noqa: S324 - npm 的 dist.shasum 就是 SHA-1
GOOD_SHA512_B64 = __import__("base64").b64encode(
    hashlib.sha512(PAYLOAD).digest()
).decode()



class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def raise_for_status(self):
        return None

    async def json(self):
        return self.payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def get(self, _url, **_kwargs):
        return _FakeResponse(self.payload)


def _patch_json_session(monkeypatch, module, payload):
    """让解析函数拿到固定的 registry 响应，不出网。"""
    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda: _FakeSession(payload))


@pytest.fixture
def artifact(tmp_path):
    path = tmp_path / "kirara_ai-3.3.0-py3-none-any.whl"
    path.write_bytes(PAYLOAD)
    return path



class TestVerification:
    def test_a_matching_sha256_passes(self, artifact):
        verify_artifact_digest(str(artifact), ArtifactDigest(sha256=GOOD_SHA256))

        assert artifact.exists(), "校验通过却把文件删了"

    def test_case_and_whitespace_do_not_matter(self, artifact):
        verify_artifact_digest(
            str(artifact), ArtifactDigest(sha256=f"  {GOOD_SHA256.upper()}  ")
        )

    def test_a_mismatch_raises_and_removes_the_file(self, artifact):
        """留着一个已知被篡改的包，下一次「重试升级」可能直接拿它。"""
        with pytest.raises(ValueError, match="校验失败"):
            verify_artifact_digest(str(artifact), ArtifactDigest(sha256="0" * 64))

        assert not artifact.exists(), "不匹配的下载文件必须删掉"

    def test_a_missing_expected_digest_is_refused(self, artifact):
        """拿不到 registry 声明的哈希就不能装。

        「没人告诉我该是什么」不等于「它是对的」。把缺失当通过，
        等于给攻击者一个「只要别声明哈希」的绕过口——而镜像地址是用户可配的，
        投毒者完全可以自己决定不声明。
        """
        with pytest.raises(ValueError, match="未声明"):
            verify_artifact_digest(str(artifact), ArtifactDigest())

        with pytest.raises(ValueError, match="未声明"):
            verify_artifact_digest(str(artifact), None)

    def test_npm_sha1_shasum_is_accepted(self, artifact):
        """npm 的 `dist.shasum` 是 SHA-1。它弱，但**有**比没有强得多：
        它仍然能挡住「镜像返回了完全不同的另一个包」这个主要威胁。"""
        verify_artifact_digest(str(artifact), ArtifactDigest(sha1=GOOD_SHA1))

    def test_npm_sri_integrity_is_accepted(self, artifact):
        verify_artifact_digest(
            str(artifact), ArtifactDigest(integrity=f"sha512-{GOOD_SHA512_B64}")
        )

    def test_a_bad_sri_integrity_is_rejected(self, artifact):
        with pytest.raises(ValueError, match="校验失败"):
            verify_artifact_digest(
                str(artifact), ArtifactDigest(integrity="sha512-" + "A" * 88)
            )

    def test_the_strongest_available_digest_is_used(self, artifact):
        """同时给了 SHA-1 与 SHA-256 时，SHA-256 说不匹配就必须失败——
        不能因为弱的那个通过了就放行。"""
        with pytest.raises(ValueError, match="校验失败"):
            verify_artifact_digest(
                str(artifact),
                ArtifactDigest(sha1=GOOD_SHA1, sha256="0" * 64),
            )

    def test_an_unparseable_integrity_string_is_not_silently_skipped(self, artifact):
        """看不懂的 integrity 不能当成「没有声明哈希」而放行，
        也不能当成通过——两种都会让一次投毒安静地成功。"""
        with pytest.raises(ValueError):
            verify_artifact_digest(
                str(artifact), ArtifactDigest(integrity="not-an-sri-string")
            )

    def test_a_missing_file_raises_rather_than_passing(self, tmp_path):
        with pytest.raises(ValueError):
            verify_artifact_digest(
                str(tmp_path / "nope.whl"), ArtifactDigest(sha256=GOOD_SHA256)
            )


class TestDigestParsing:
    def test_pypi_hashes_are_read(self):
        digest = ArtifactDigest.from_pypi({"hashes": {"sha256": GOOD_SHA256}})

        assert digest is not None
        assert digest.sha256 == GOOD_SHA256

    def test_pypi_without_hashes_yields_an_empty_digest(self):
        """空摘要不是 `None`：调用方必须走到「未声明」那条拒绝分支，
        而不是因为拿到 `None` 就跳过校验。"""
        digest = ArtifactDigest.from_pypi({"filename": "x.whl"})

        assert digest is not None
        assert not digest

    def test_npm_dist_is_read(self):
        digest = ArtifactDigest.from_npm(
            {"shasum": GOOD_SHA1, "integrity": f"sha512-{GOOD_SHA512_B64}"}
        )

        assert digest is not None
        assert digest.sha1 == GOOD_SHA1
        assert digest.integrity.startswith("sha512-")

    def test_garbage_input_does_not_crash(self):
        assert not ArtifactDigest.from_pypi(None)
        assert not ArtifactDigest.from_npm("nonsense")


class TestTheInstallPathActuallyVerifies:
    """`verify_artifact_digest` 正确但无人调用，等于没做。

    这是本轮反复在修的那类缺陷，而在这里它的后果最重：一个「看起来校验过」的
    升级流程会把镜像给的任意 wheel 装进生产环境。
    """

    @pytest.mark.asyncio
    async def test_the_resolver_propagates_the_registry_digest(self, monkeypatch):
        """解析层必须把 registry 声明的哈希带出来。

        期望值取不到，下游就只能在「拒绝一切升级」和「不校验」之间选，
        而那两个都不可接受。

        断言的是**行为**而不是源码里出现某个标识符：后者会在函数被拆分或改名时
        给出错误答案（一次已经发生过——它盯着 `get_latest_pypi_version` 的源码，
        而带摘要的逻辑搬到了 `resolve_pypi_release`）。
        """
        from kirara_ai.web.api.system import utils

        payload = {
            "files": [
                {
                    "filename": "kirara_ai-9.9.9-py3-none-any.whl",
                    "url": "https://example.invalid/kirara_ai-9.9.9-py3-none-any.whl",
                    "hashes": {"sha256": GOOD_SHA256},
                }
            ]
        }
        _patch_json_session(monkeypatch, utils, payload)

        version, url, digest = await utils.resolve_pypi_release("kirara-ai", "https://i.invalid/simple")

        assert version == "9.9.9"
        assert url.endswith(".whl")
        assert digest.sha256 == GOOD_SHA256, "registry 的 sha256 没有被带出来"

    @pytest.mark.asyncio
    async def test_the_npm_resolver_propagates_the_dist_digest(self, monkeypatch):
        from kirara_ai.web.api.system import utils

        payload = {
            "dist-tags": {"beta": "1.2.3"},
            "versions": {
                "1.2.3": {
                    "dist": {
                        "tarball": "https://example.invalid/webui-1.2.3.tgz",
                        "shasum": GOOD_SHA1,
                        "integrity": f"sha512-{GOOD_SHA512_B64}",
                    }
                }
            },
        }
        _patch_json_session(monkeypatch, utils, payload)

        version, url, digest = await utils.resolve_npm_release(
            "kirara-ai-webui", "https://r.invalid", dist_tag="beta"
        )

        assert version == "1.2.3"
        assert url.endswith(".tgz")
        assert digest.sha1 == GOOD_SHA1
        assert digest.integrity.startswith("sha512-")

    @pytest.mark.asyncio
    async def test_a_registry_without_hashes_yields_an_empty_digest(self, monkeypatch):
        """没有哈希时带出空摘要，让 `verify_artifact_digest` 去拒绝安装。

        这里**不能**返回一个「假装通过」的值：镜像地址是用户可配的，
        投毒者完全可以选择不声明哈希。
        """
        from kirara_ai.web.api.system import utils

        payload = {
            "files": [
                {
                    "filename": "kirara_ai-9.9.9-py3-none-any.whl",
                    "url": "https://example.invalid/kirara_ai-9.9.9-py3-none-any.whl",
                }
            ]
        }
        _patch_json_session(monkeypatch, utils, payload)

        _, _, digest = await utils.resolve_pypi_release("kirara-ai", "https://i.invalid/simple")

        assert not digest

    def test_the_update_route_verifies_before_installing(self):
        """校验必须发生在 `pip install` / 解包**之前**。

        装完再校验没有意义：代码已经在机器上了。
        """
        import inspect

        from kirara_ai.web.api.system import routes

        source = inspect.getsource(routes.perform_update)
        assert "verify_artifact_digest" in source, "升级路径没有校验下载内容"

        verify_at = source.index("verify_artifact_digest")
        install_at = source.index('"install"')
        assert verify_at < install_at, "校验发生在安装之后，那时代码已经落地了"

        unpack_at = source.index("install_webui_archive")
        assert source.index("verify_artifact_digest", verify_at + 1) < unpack_at, (
            "WebUI 包在校验前就被解开了"
        )


    def test_a_tampered_backend_package_is_not_installed(
        self, tmp_path, test_client, auth_headers
    ):
        """端到端：镜像返回的内容与它声明的哈希不符时，绝不能执行安装。

        这是整条加固的目的。上面那些用例各自证明一环，这一条证明**合起来**能挡住
        真实攻击形态：镜像地址是用户可配的，投毒者控制字节但改不了 PyPI 声明的
        哈希（若它也能改，那就是 PyPI 本身被攻破，超出本项目的威胁模型）。

        不 mock `verify_artifact_digest`——mock 掉它，这个用例就只能证明
        「调用发生了」，而不能证明「拦住了」。
        """
        from unittest.mock import AsyncMock, patch

        tampered = tmp_path / "kirara_ai-9.9.9-py3-none-any.whl"
        tampered.write_bytes(b"malicious payload, not the declared artifact")

        installs: list[list[str]] = []

        def _record_install(cmd, **_kwargs):
            installs.append(list(cmd))
            raise AssertionError("被篡改的包仍然被安装了")

        with patch(
            "kirara_ai.web.api.system.routes.get_installed_version",
            return_value="3.3.0b7",
        ), patch(
            "kirara_ai.web.api.system.routes.resolve_pypi_release",
            AsyncMock(
                return_value=(
                    "9.9.9",
                    "https://mirror.invalid/kirara_ai-9.9.9-py3-none-any.whl",
                    ArtifactDigest(sha256=GOOD_SHA256),
                )
            ),
        ), patch(
            "kirara_ai.web.api.system.routes.download_file",
            AsyncMock(return_value=(str(tampered), "irrelevant")),
        ), patch(
            "kirara_ai.web.api.system.routes.subprocess.run", _record_install
        ):
            response = test_client.post(
                "/backend-api/api/system/update",
                headers=auth_headers,
                json={"update_backend": True, "update_webui": False},
            )

        assert response.status_code == 500, response.text
        assert not installs, "pip install 被执行了"
        assert "校验失败" in response.json()["message"]
        assert not tampered.exists(), "被篡改的包没有被删掉"
