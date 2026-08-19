import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from kirara_ai.web import utils


def _write_archive(path: Path, members: list[tuple[tarfile.TarInfo, bytes]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for member, contents in members:
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents) if contents else None)


def _file(name: str, contents: bytes) -> tuple[tarfile.TarInfo, bytes]:
    return tarfile.TarInfo(name), contents


def test_install_webui_archive_writes_version_metadata_and_replaces_old_files(
    tmp_path: Path,
):
    archive = tmp_path / "webui.tgz"
    install = tmp_path / "web"
    install.mkdir()
    (install / "old.js").write_text("old", encoding="utf-8")
    _write_archive(
        archive,
        [
            _file("package/dist/index.html", b"<html></html>"),
            _file(
                "package/dist/version.json",
                b'{"version":"v3.3.0b8","packageVersion":"stale"}',
            ),
            _file("package/dist/assets/app.js", b"console.log('ok')"),
        ],
    )

    utils.install_webui_archive(archive, install, "3.3.0-b8")

    metadata = json.loads((install / "version.json").read_text(encoding="utf-8"))
    assert metadata == {"version": "v3.3.0b8", "packageVersion": "3.3.0-b8"}
    assert (install / "assets" / "app.js").is_file()
    assert not (install / "old.js").exists()
    assert utils.get_installed_webui_version(install) == "3.3.0-b8"


@pytest.mark.parametrize(
    "member_name",
    [
        "package/dist/../../outside.txt",
        r"package/dist/..\outside.txt",
        "/package/dist/outside.txt",
    ],
)
def test_install_webui_archive_rejects_path_traversal(
    tmp_path: Path, member_name: str
):
    archive = tmp_path / "webui.tgz"
    install = tmp_path / "web"
    _write_archive(
        archive,
        [
            _file("package/dist/index.html", b"ok"),
            _file(member_name, b"outside"),
        ],
    )

    with pytest.raises(ValueError, match="unsafe WebUI archive member"):
        utils.install_webui_archive(archive, install, "3.3.0-b8")

    assert not (tmp_path / "outside.txt").exists()
    assert not install.exists()


@pytest.mark.parametrize("link_type", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_install_webui_archive_rejects_symbolic_and_hard_links(
    tmp_path: Path, link_type: bytes
):
    archive = tmp_path / "webui.tgz"
    install = tmp_path / "web"
    link = tarfile.TarInfo("package/dist/assets/link.js")
    link.type = link_type
    link.linkname = "../../outside.js"
    _write_archive(
        archive,
        [_file("package/dist/index.html", b"ok"), (link, b"")],
    )

    with pytest.raises(ValueError, match="unsupported WebUI archive member"):
        utils.install_webui_archive(archive, install, "3.3.0-b8")

    assert not install.exists()


def test_install_webui_archive_restores_existing_install_when_swap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    archive = tmp_path / "webui.tgz"
    install = tmp_path / "web"
    install.mkdir()
    (install / "index.html").write_text("old", encoding="utf-8")
    _write_archive(archive, [_file("package/dist/index.html", b"new")])
    real_replace = os.replace
    replace_calls = 0

    def fail_second_replace(source, destination):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("simulated staging swap failure")
        return real_replace(source, destination)

    monkeypatch.setattr(utils.os, "replace", fail_second_replace)

    with pytest.raises(OSError, match="simulated staging swap failure"):
        utils.install_webui_archive(archive, install, "3.3.0-b8")

    assert (install / "index.html").read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".web-stage-*"))
    assert not list(tmp_path.glob(".web-backup-*"))


@pytest.mark.parametrize("contents", [b"[]", b"null", b'"legacy"'])
def test_non_object_webui_metadata_is_replaced_with_a_valid_object(
    tmp_path: Path, contents: bytes
):
    archive = tmp_path / "webui.tgz"
    install = tmp_path / "web"
    _write_archive(
        archive,
        [
            _file("package/dist/index.html", b"ok"),
            _file("package/dist/version.json", contents),
        ],
    )

    utils.install_webui_archive(archive, install, "3.3.0-b8")

    metadata = json.loads((install / "version.json").read_text(encoding="utf-8"))
    assert metadata == {"version": "3.3.0-b8", "packageVersion": "3.3.0-b8"}


@pytest.mark.parametrize("contents", ["[]", "null", '"legacy"', "{}", "not-json"])
def test_invalid_installed_webui_metadata_reports_unknown(
    tmp_path: Path, contents: str
):
    install = tmp_path / "web"
    install.mkdir()
    (install / "version.json").write_text(contents, encoding="utf-8")

    assert utils.get_installed_webui_version(install) == "unknown"
