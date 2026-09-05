"""每个渠道身份只能读写自己那间房。

为什么这组测试是安全边界而不是功能验证
----------------------------------
需求是「用户也能用 Agent 编辑文件，但对创建者的 VPS 最多只产生临时文件」。
沙箱是兑现后半句的唯一机制，因此它的失效方式必须逐个钉住——一次路径穿越
就等于把 `config.yaml`（含 API Key）和 `web/password.hash` 交出去。

`relative` 是**用户可控输入**：它来自模型的工具调用参数，而模型的输入来自
聊天消息。所以下面每一条都按「攻击者能构造任意字符串」来写。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kirara_ai.agent_runtime.workspace import (
    WorkspaceService,
    WorkspaceViolation,
    identity_fingerprint,
)


@pytest.fixture
def service(tmp_path: Path) -> WorkspaceService:
    return WorkspaceService(tmp_path)


class TestPathsCannotEscapeTheRoom:
    def test_a_plain_relative_path_resolves_inside(self, service: WorkspaceService):
        room = service.for_identity("telegram", "123")
        room.ensure()

        target = room.resolve("notes/report.xlsx")

        assert target.is_relative_to(room.root.resolve())

    @pytest.mark.parametrize(
        "attack",
        [
            "../escaped.txt",
            "../../escaped.txt",
            "../../../../../../etc/passwd",
            "notes/../../escaped.txt",
            "./../escaped.txt",
            "a/b/c/../../../../escaped.txt",
        ],
    )
    def test_dot_dot_traversal_is_refused(self, service: WorkspaceService, attack: str):
        """`..` 必须在 resolve **之后**判。

        resolve 之前做字符串检查是无效的：`a/../../x` 里没有任何可疑子串，
        而拼接后的绝对路径才暴露它指向房外。
        """
        room = service.for_identity("telegram", "123")
        room.ensure()

        with pytest.raises(WorkspaceViolation):
            room.resolve(attack)

    @pytest.mark.parametrize(
        "attack",
        ["/etc/passwd", "/app/data/config.yaml", r"C:\Windows\System32\config"],
    )
    def test_absolute_paths_are_refused_not_reinterpreted(
        self, service: WorkspaceService, attack: str
    ):
        """绝对路径拒绝而不是「截断成相对路径」。

        截断会把 `/etc/passwd` 悄悄变成 `<房间>/etc/passwd`：用户以为读到了
        系统文件、实际读到一个不存在的路径，而报错说的是「文件不存在」——
        一个把安全拒绝伪装成普通错误的行为。
        """
        room = service.for_identity("telegram", "123")
        room.ensure()

        with pytest.raises(WorkspaceViolation):
            room.resolve(attack)

    @pytest.mark.skipif(
        not hasattr(os, "symlink") or os.name == "nt",
        reason="Windows 上建符号链接需要管理员权限",
    )
    def test_a_symlink_pointing_outside_is_refused(
        self, service: WorkspaceService, tmp_path: Path
    ):
        """符号链接里连 `..` 都没有，只有 resolve 才能发现它指向房外。

        这一条是「为什么必须 resolve」最直接的证据：纯字符串规则对它完全无效。
        """
        room = service.for_identity("telegram", "123")
        room.ensure()
        secret = tmp_path / "outside-secret.txt"
        secret.write_text("api-key", encoding="utf-8")
        (room.root / "link").symlink_to(secret)

        with pytest.raises(WorkspaceViolation):
            room.resolve("link")

    def test_the_room_itself_resolves(self, service: WorkspaceService):
        """`.` 指向房间本身，应当允许——列目录是正当操作。"""
        room = service.for_identity("telegram", "123")
        room.ensure()

        assert room.resolve(".") == room.root.resolve()

    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_empty_paths_are_refused(self, service: WorkspaceService, empty):
        room = service.for_identity("telegram", "123")

        with pytest.raises(WorkspaceViolation):
            room.resolve(empty)


class TestIdentitiesAreIsolatedFromEachOther:
    def test_two_senders_get_different_rooms(self, service: WorkspaceService):
        a = service.for_identity("telegram", "111")
        b = service.for_identity("telegram", "222")

        assert a.root != b.root

    def test_the_same_number_on_two_channels_does_not_share_a_room(
        self, service: WorkspaceService
    ):
        """QQ 号与 Telegram 用户 ID 可能是同一串数字。

        只摘发送者标识会让两个渠道的同号用户共用一间房——那是跨渠道数据泄漏，
        而两边都看不出异常。
        """
        qq = service.for_identity("onebot", "123456")
        telegram = service.for_identity("telegram", "123456")

        assert qq.fingerprint != telegram.fingerprint

    def test_one_identity_cannot_reach_another_room(self, service: WorkspaceService):
        """相邻房间之间同样不可达——`../<别人的指纹>` 也是穿越。"""
        a = service.for_identity("telegram", "111")
        b = service.for_identity("telegram", "222")
        a.ensure()
        b.ensure()

        with pytest.raises(WorkspaceViolation):
            a.resolve(f"../{b.fingerprint}/stolen.txt")


class TestTheFingerprintDoesNotLeakTheIdentity:
    def test_the_raw_sender_id_never_appears_in_the_path(
        self, service: WorkspaceService
    ):
        """目录名里出现 QQ 号会让「谁在用这个机器人」一次 `ls` 就读得出。

        那份名单本身就是隐私，与文件内容是否泄漏无关。
        """
        room = service.for_identity("onebot", "1726256417")

        assert "1726256417" not in str(room.root)

    def test_the_fingerprint_is_stable(self, service: WorkspaceService):
        """同一个人两次拿到同一间房，否则他的文件每次都"消失"。"""
        first = service.for_identity("telegram", "123")
        second = service.for_identity("telegram", "123")

        assert first.fingerprint == second.fingerprint

    def test_the_fingerprint_length_is_fixed(self):
        """定长摘要：原始 ID 的长度差异会让短号用户在目录列表里一眼可辨。"""
        short = identity_fingerprint("telegram", "1")
        long = identity_fingerprint("telegram", "1" * 200)

        assert len(short) == len(long)

    def test_account_scope_can_isolate_further_but_is_off_by_default(self):
        """默认不按机器人账号隔离：同一个人找两个机器人通常期望看到同一批文件。"""
        default = identity_fingerprint("telegram", "123")
        same = identity_fingerprint("telegram", "123")
        scoped = identity_fingerprint("telegram", "123", account_scope="bot-a")

        assert default == same
        assert scoped != default


class TestTheCreatorsFilesAreOutsideEveryRoom:
    def test_config_and_credentials_live_outside_the_workspaces_root(
        self, service: WorkspaceService, tmp_path: Path
    ):
        """这一条说明沙箱为什么够用：敏感文件根本不在 workspaces 之下。

        因此「用户能在自己房间里任意读写」与「用户能读 API Key」是两件
        互不相干的事——不需要额外的黑名单去保护 config.yaml。
        """
        for sensitive in ("config.yaml", "web/password.hash", "db/kirara.db"):
            path = (tmp_path / sensitive).resolve()
            assert not path.is_relative_to(service.root.resolve()), sensitive
