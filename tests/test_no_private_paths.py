"""跟踪文件里不得出现操作者本机的私有路径（需求 18.2 / 23.4）。

「不得把私有数据写入源码、测试、日志、截图与 README」这一条，最容易漏的不是
凭据而是**路径**：`C:\\Users\\<用户名>\\...` 会暴露操作者的用户名与目录结构，
而它往往是顺手写进夹具或计划文档的。

这条测试之前不存在，实际后果是三份跟踪文档与一个测试夹具各带一处私有路径，
其中最讽刺的一处是「断言输出里不含私有路径」的那个断言本身用了真实路径。

同时也钉住凭据类模式——两者都属于「发布前不得外泄」，放在同一道门禁里。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 二进制与大文件跳过：它们不参与文本扫描。
_SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".ttf", ".woff", ".woff2",
    ".whl", ".zip", ".db", ".sqlite", ".sqlite3",
}
_MAX_BYTES = 2_000_000

#: 形如 `C:\Users\<name>` / `C:/Users/<name>` 的 Windows 家目录路径。
#:
#: 白名单里的名字是**通用占位**而不是真人用户名：文档需要举例说明路径形态时
#: 用它们，既能表达清楚又不泄露任何人的身份。
_HOME_PATH_PATTERN = re.compile(
    r"C:[\\/]{1,2}Users[\\/]{1,2}(?!<)([A-Za-z0-9_.-]+)", re.IGNORECASE
)
_ALLOWED_HOME_NAMES = {
    "operator",     # 夹具里的合成用户名
    "example",
    "user",
    "youruser",
    "runneradmin",  # GitHub Actions Windows runner 的固定账户
}

#: 凭据类模式。占位符（`<token>`、`your-api-key`）不匹配，因为它们不含实值。
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI 风格密钥", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    (
        "实值 Bearer 令牌",
        re.compile(r"Bearer\s+(?!<)[A-Za-z0-9_\-.]{24,}"),
    ),
    ("私钥", re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY")),
    (
        "QQ 会话 Cookie",
        re.compile(r"\b(?:skey|p_skey|qrsig|p_uin)\s*[:=]\s*[A-Za-z0-9]{8,}"),
    ),
)


def _tracked_text_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("git 不可用，无法枚举跟踪文件")
    paths = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        path = PROJECT_ROOT / line
        if not path.is_file() or path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        try:
            if path.stat().st_size > _MAX_BYTES:
                continue
        except OSError:
            continue
        paths.append(path)
    return paths


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def test_no_operator_home_directory_paths_in_tracked_files():
    """跟踪文件不得暴露真人用户名的家目录路径。

    需要说明路径形态时，用 `<用户主目录>` 或白名单里的通用占位名。
    """
    offenders: list[str] = []
    for path in _tracked_text_files():
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        for index, line in enumerate(_read(path).splitlines(), start=1):
            for match in _HOME_PATH_PATTERN.finditer(line):
                if match.group(1).lower() in _ALLOWED_HOME_NAMES:
                    continue
                offenders.append(f"{relative}:{index}: {match.group(0)}")
    assert not offenders, (
        "以下跟踪文件包含操作者本机的家目录路径，会随仓库分发；"
        f"请改为 `<用户主目录>` 或通用占位名：{offenders[:10]}"
    )


@pytest.mark.parametrize(("label", "pattern"), _SECRET_PATTERNS)
def test_no_credential_values_in_tracked_files(label: str, pattern: re.Pattern[str]):
    """跟踪文件不得包含凭据实值（占位符不算）。"""
    offenders: list[str] = []
    for path in _tracked_text_files():
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        # 本文件自身定义这些模式，跳过以免自我命中。
        if relative == "tests/test_no_private_paths.py":
            continue
        for index, line in enumerate(_read(path).splitlines(), start=1):
            match = pattern.search(line)
            if match:
                offenders.append(f"{relative}:{index}")
    assert not offenders, f"以下跟踪文件疑似包含{label}：{offenders[:10]}"


def test_the_allowlist_stays_small():
    """白名单只放通用占位名。

    把一个真人用户名加进白名单就等于取消这道门禁，因此这里限制它的规模，
    让「顺手加一个名字」这个动作必须先改断言、被人看见。
    """
    assert len(_ALLOWED_HOME_NAMES) <= 6
    assert "devin" not in _ALLOWED_HOME_NAMES
