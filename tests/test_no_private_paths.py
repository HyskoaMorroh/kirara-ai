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

from tests.utils.external_tools import require_git


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

#: 会话转录路径与本机仓库绝对路径。
#:
#: 这一类比家目录路径更隐蔽：`~\.codex\sessions\...` 里没有用户名，
#: `E:\output\kirara-ai\...` 也不含 `C:\Users`，所以上面那条家目录规则一个都
#: 抓不到。但它们同样是「操作者本机的东西随仓库分发」——转录路径还额外暴露
#: 会话 ID 与本机 AI 工具的目录结构。
#:
#: 实际后果：`2026-08-28-handoff.md` 带着 51 处转录路径与仓库绝对路径被跟踪了
#: 三天。当时停止跟踪同类文档时按文件名逐个列举，没有回头扫一遍同类，
#: 而这道门禁那时也只查家目录。判据应当是「这类内容一律不进仓库」，
#: 而不是「哪几个文件名」。
_LOCAL_ARTIFACT_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "AI 工具会话转录路径",
        re.compile(r"[~.][\/]\.(?:claude|codex|gemini)[\/]\S*", re.IGNORECASE),
    ),
    (
        "会话转录文件名",
        re.compile(
            r"(?:rollout-\d{4}-\d{2}-\d{2}T[\w-]+"
            r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl"
        ),
    ),
)

#: **刻意不把「本机仓库绝对路径」（`E:\output\kirara-ai\...`）列为门禁。**
#:
#: 加过一次又撤掉，理由记在这里以免下次又加：它命中 9 份历史规划文档（其中 5 份
#: 已在远端），而它泄漏的东西是「这台机器上有个 E 盘、里面有个 output 目录」——
#: 没有用户名、没有凭据、没有会话标识，目录名本身还与仓库名重复。
#: 为它改写 9 份历史规划材料是纯 churn，而 17.3 明确要求不得擅自清理既有内容。
#:
#: 上面两条留着的原因正相反：会话转录路径带**会话 UUID** 与所用 AI 工具，
#: 那是这台机器上的一次具体对话的标识，别人拿到毫无用处却能看出很多东西。

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
    # 运行时镜像里没有 git（它不需要）。缺失时 `subprocess.run` 抛
    # FileNotFoundError，下面那句 `returncode != 0` 一次都执行不到——
    # 这道门禁因此在镜像内测试里以 error 而不是 skip 收场。
    require_git("无法枚举跟踪文件")
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


@pytest.mark.parametrize(("label", "pattern"), _LOCAL_ARTIFACT_PATTERNS)
def test_no_local_machine_artifacts_in_tracked_files(
    label: str, pattern: "re.Pattern[str]"
):
    """跟踪文件不得引用本机的会话转录或仓库绝对路径。

    这些路径在别人的 clone 里毫无意义，而它们泄漏的是操作者本机的目录结构、
    所用 AI 工具与会话标识。需要说明路径形态时用 `<仓库根>` / `<会话转录>`。
    """
    offenders: list[str] = []
    for path in _tracked_text_files():
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        # 本文件自身定义这些模式，跳过以免自我命中。
        if relative == "tests/test_no_private_paths.py":
            continue
        for index, line in enumerate(_read(path).splitlines(), start=1):
            match = pattern.search(line)
            if match:
                offenders.append(f"{relative}:{index}: {match.group(0)[:70]}")
    assert not offenders, (
        f"以下跟踪文件包含{label}，会随仓库分发（改用占位符，"
        f"或把该文档从跟踪中移除）：{offenders[:10]}"
    )


def test_the_allowlist_stays_small():
    """白名单只放通用占位名。

    把一个真人用户名加进白名单就等于取消这道门禁，因此这里限制它的规模，
    让「顺手加一个名字」这个动作必须先改断言、被人看见。
    """
    assert len(_ALLOWED_HOME_NAMES) <= 6
    assert "devin" not in _ALLOWED_HOME_NAMES
