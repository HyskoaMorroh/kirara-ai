"""每个渠道身份一间自己的工作空间。

为什么需要它
----------
需求是「用户也能用 Agent 管理文件，但对创建者的 VPS 最多只产生临时文件」。
当前的权限模型是**二元的**：`principal_can_control_agent()` 要么给全部工具、
要么给空集。于是「让用户生成一个 Excel」与「让用户改 config.yaml」
在代码里是同一件事——放开一个就放开另一个。

这个模块把「能不能动文件」拆成「能动**哪些**文件」：每个渠道身份拿到
`data/workspaces/<身份指纹>/` 下的一间房，读写都被钉在那间房里。
创建者的配置、数据库、凭据文件都在 workspaces 之外，因此不在任何用户的可达范围内。

三条刻意的设计
------------
1. **身份指纹用摘要而不是原始 ID。** 目录名里出现 QQ 号或 Telegram 用户 ID
   会让「谁在用这个机器人」变成一次 `ls` 就能读出的东西，而那份名单本身
   就是隐私。摘要不可逆，且长度固定——原始 ID 的长度差异会让短号用户
   在目录列表里一眼可辨。

2. **`..` 与符号链接都必须在 resolve 之后判。** 判据是
   `resolved.is_relative_to(room)`，与 `workflow/persistence.py:197` 同一套写法。
   在 resolve **之前**做字符串检查是无效的：`a/../../etc/passwd` 里没有任何
   可疑子串，而 `room/link-to-root` 这种符号链接连 `..` 都没有。

3. **房间不存在时按需创建，但绝不自动创建父目录之外的东西。**
   `mkdir(parents=True)` 在 workspaces 根被误配成一个不存在的深路径时
   会静默造出整条目录树，那会让「配错了」变成「在错的地方跑起来了」。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: 身份指纹的长度。16 个十六进制字符 = 64 位，碰撞概率对这个用量足够低，
#: 而完整的 64 字符会让路径长度在 Windows 上逼近 260 的上限。
_FINGERPRINT_LENGTH = 16

#: 工作空间根目录名。放在 `DATA_PATH` 下面，因此随 compose 的单条挂载一起持久化。
WORKSPACES_DIRNAME = "workspaces"


class WorkspaceViolation(Exception):
    """A path escaped its owner's workspace, or the workspace root is unusable."""


@dataclass(frozen=True)
class Workspace:
    """One channel identity's private room on the server."""

    #: 身份指纹（不可逆摘要），也是目录名。
    fingerprint: str
    #: 这间房的绝对路径。
    root: Path

    def resolve(self, relative: str) -> Path:
        """把一个用户给的相对路径解析成绝对路径，并确认它没逃出这间房。

        `relative` 是**用户可控输入**：它来自模型的工具调用参数，而模型的输入
        来自聊天消息。因此这里假定它可以是任意字符串，包括 `../../etc/passwd`、
        绝对路径、以及指向房外的符号链接。
        """
        if not isinstance(relative, str) or not relative.strip():
            raise WorkspaceViolation("路径不能为空")

        candidate = Path(relative)
        if candidate.is_absolute():
            # 绝对路径一律拒绝而不是"截断成相对路径"：后者会把
            # `/etc/passwd` 悄悄变成 `<房间>/etc/passwd`，用户以为读到了系统文件、
            # 实际读到一个不存在的路径，而报错说的是"文件不存在"。
            raise WorkspaceViolation(
                f"只能使用相对路径（收到绝对路径 {relative!r}）"
            )

        target = (self.root / candidate).resolve()
        room = self.root.resolve()
        if target != room and not target.is_relative_to(room):
            raise WorkspaceViolation(
                f"路径 {relative!r} 超出了你的工作空间"
            )
        return target

    def ensure(self) -> Path:
        """确保这间房存在，返回它的路径。"""
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root


def identity_fingerprint(
    channel_type: str,
    sender_scope: str,
    *,
    account_scope: Optional[str] = None,
) -> str:
    """Return a stable, non-reversible fingerprint for one channel identity.

    渠道类型一并进摘要：QQ 号与 Telegram 用户 ID 可能是同一串数字，
    只摘发送者标识会让两个渠道的同号用户共用一间房——那是跨渠道的数据泄漏，
    且两边都看不出异常。

    `account_scope`（经由哪个机器人账号收到）默认**不进**摘要：同一个人从
    同一个渠道找两个不同的机器人，通常期望看到同一批文件。需要按账号隔离时
    显式传入。
    """
    parts = [str(channel_type or "unknown"), str(sender_scope or "unknown")]
    if account_scope:
        parts.append(str(account_scope))
    raw = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:_FINGERPRINT_LENGTH]


class WorkspaceService:
    """Resolve and create per-identity workspaces under one fixed root."""

    def __init__(self, data_path: str | os.PathLike[str]) -> None:
        self._root = Path(data_path) / WORKSPACES_DIRNAME

    @property
    def root(self) -> Path:
        """工作空间总根目录。创建者的配置与数据库都在它**之外**。"""
        return self._root

    def for_identity(
        self,
        channel_type: str,
        sender_scope: str,
        *,
        account_scope: Optional[str] = None,
    ) -> Workspace:
        fingerprint = identity_fingerprint(
            channel_type, sender_scope, account_scope=account_scope
        )
        return Workspace(fingerprint=fingerprint, root=self._root / fingerprint)

    def for_context(self, context: object) -> Workspace:
        """从 `ChannelContext` 取身份。

        用 `getattr` 而不是类型标注导入：`ChannelContext` 住在
        `agent_runtime.core` 里，而那个模块会 import 整个执行器——
        本模块被工具层引用，反向依赖会形成环。
        """
        return self.for_identity(
            str(getattr(context, "channel_type", "") or "unknown"),
            str(getattr(context, "sender_scope", "") or "unknown"),
        )
