"""`.dockerignore` 必须按**路径**生效，而不只是「字符串出现在文件里」。

现有的 `test_docker_context_excludes_local_audit_and_runtime_state` 断言的是
「这些 pattern 行存在」。这条断言无法发现 Docker 的匹配语义问题：
`.dockerignore` 的 pattern 是对**完整相对路径**做 `filepath.Match`，
不是对每一级路径分量。因此 `__pycache__` 只能挡住根目录下的
`__pycache__/`，挡不住 `kirara_ai/**/__pycache__/`；`*.log` 只能挡根目录的
`.log`，挡不住 `webui/.playwright-cli/run.log`。

需求 23.3 要求「镜像必须排除 docs/LOGO.jpg、私有数据、密钥、缓存、.venv、
PATHFINDER-2026-08-21/ 和测试临时产物」。缓存这一类正是被上面这个语义差
漏掉的：`Dockerfile` 第一段 `COPY . /source`（version-check 阶段）会把整个
构建上下文上传，1300 多个 `.pyc` 与 1 MB 级的 `tsbuildinfo` 都在其中。

这里直接模拟 Docker 的匹配算法逐路径判定，因此新增一条 pattern 却写成
根锚定形式时会立刻失败。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate one dockerignore pattern into a full-path regex.

    与 Docker 的 `filepath.Match` 语义对齐：`**` 跨目录，`*` 不跨目录，
    `?` 匹配单个非分隔符字符。
    """
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                out.append(".*")
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    index += 1
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char in ".+^$(){}|[]\\":
            out.append("\\" + char)
        else:
            out.append(char)
        index += 1
    return re.compile("^" + "".join(out) + "$")


def _rules() -> list[tuple[bool, re.Pattern[str]]]:
    text = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    rules: list[tuple[bool, re.Pattern[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        body = line[1:] if negated else line
        rules.append((negated, _pattern_to_regex(body.rstrip("/"))))
    return rules


def _is_excluded(relative_path: str, rules: list[tuple[bool, re.Pattern[str]]]) -> bool:
    """Docker 对每一条 pattern 依序求值，后者覆盖前者；祖先命中即整棵剪掉。"""
    excluded = False
    parts = relative_path.split("/")
    ancestors = ["/".join(parts[: depth + 1]) for depth in range(len(parts))]
    for negated, pattern in rules:
        if any(pattern.match(candidate) for candidate in ancestors):
            excluded = not negated
    return excluded


#: 必须被排除的路径，按 23.3 点名的类别分组。全部使用**嵌套**路径，
#: 因为根目录那一层本来就能被根锚定 pattern 挡住。
MUST_EXCLUDE = (
    # Python 字节码缓存：`COPY . /source` 会把它们全部上传
    "kirara_ai/__pycache__/entry.cpython-311.pyc",
    "kirara_ai/plugins/im_onebot_adapter/__pycache__/adapter.cpython-311.pyc",
    "scripts/__pycache__/version.cpython-311.pyc",
    "tests/__pycache__/conftest.cpython-311.pyc",
    # 构建缓存
    "webui/tsconfig.app.tsbuildinfo",
    "webui/node_modules/vue/package.json",
    ".ruff_cache/0.1.0/12345",
    # 嵌套日志与浏览器自动化留痕
    "webui/.playwright-cli/run.log",
    "webui/.playwright-mcp/page.yml",
    "logs/kirara.log",
    # 规划与交接材料不属于运行时
    "docs/superpowers/plans/2026-08-28-handoff.md",
    "docs/superpowers/specs/2026-08-24-kirara-agent-runtime-design.md",
    # 用户私有文件与凭据
    "docs/LOGO.jpg",
    "test_password.hash",
    "data/web/password.hash",
    "data/web/creator.subject",
    # 原始计划红线
    "PATHFINDER-2026-08-21/00-features.md",
    # 本地审计产物与运行期数据库
    ".qa-final-20260826/db/kirara.db",
    ".memsearch/memory/2026-08-23.md",
    "work/req/req-007.md",
    # 界面核对用的 QA 截图：只是本机排版留痕，运行时不读它们，
    # 而 `COPY . /source` 会把它们逐张塞进构建上下文。
    "resource-desktop.png",
    "resource-mobile.png",
    "resource-mobile-fixed.png",
    # 已构建的前端产物：镜像由 Dockerfile 从 webui 源码重新构建
    "web/version.json",
    "web/favicon.ico",
    # 与本项目无关的本地脚本
    "create_concise_loan_contract.js",
    "update_loan_identity.py",
    "update_loan_rate.py",
)

#: 必须**保留**的路径。排除规则收紧时最容易连带伤到它们，
#: 而它们缺失会让镜像构建失败或让文档失效。
MUST_INCLUDE = (
    "kirara_ai/entry.py",
    "kirara_ai/plugins/im_onebot_adapter/adapter.py",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "MANIFEST.in",
    "scripts/version.py",
    "webui/package.json",
    "webui/src/main.ts",
    "docker/start.sh",
    "data/workflows/chat/custom_script.yaml",
    "data/dispatch_rules/rules.yaml",
    "data/fonts/sarasa-mono-sc-regular.ttf",
    ".env.example",
)


@pytest.mark.parametrize("relative_path", MUST_EXCLUDE)
def test_build_context_excludes_path(relative_path: str):
    rules = _rules()
    assert _is_excluded(relative_path, rules), (
        f"{relative_path} 会进入 Docker 构建上下文。"
        "注意 dockerignore 的 pattern 匹配完整相对路径："
        "根锚定的 `__pycache__` / `*.log` 挡不住嵌套路径，需要 `**/` 形式。"
    )


@pytest.mark.parametrize("relative_path", MUST_INCLUDE)
def test_build_context_keeps_path(relative_path: str):
    rules = _rules()
    assert not _is_excluded(relative_path, rules), (
        f"{relative_path} 被排除了，但镜像构建或文档依赖它"
    )
