"""正则字面量里丢掉的反斜杠必须被抓住——它不会报错，只会永远匹配不上。

发现过程：`ResourceView.vue` 里两处版本号校验写成 `/^d+.d+.d+/`。
那是 `/^\\d+\\.\\d+\\.\\d+/` 被 heredoc 或编辑器吃掉反斜杠之后的样子。

这类缺陷的危险之处在于**它不是语法错误**：正则依然合法，`tsc` 不报警，
ESLint 不报警，运行时不抛异常。它只是匹配不上任何东西——
`/^d+.d+.d+/.test('1.0.0') === false`。

后果视用法而定，两种都很糟：

- 用在校验里（本次这两处）：表单对任何输入都报「版本号需形如 1.0.0」，
  整条「从纯文本创建提示词」在界面上不可用；
- 用在识别里：默默放行本该拦下的输入，或默默丢掉本该处理的内容。

而且这个缺陷能躲过 grep 式的测试：`expect(source).toContain('正文不能为空')`
看得见那行字符串，看不见它匹配不上任何东西。所以需要一条按字符扫描的守卫。

判据：正则字面量（JS/TS/Vue）或 `re.*()` 的模式串（Python）里，
出现前面没有反斜杠的 `d+` / `w+` / `s+` / `b+` 及其大写形式。
这些字母紧跟 `+` 的组合在真实正则里几乎只可能是 `\\d+` 这类的残骸——
真要匹配字面的 "d" 后跟一个或多个 "d"，写法会是 `dd*` 或 `[d]+`。
"""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: JS/TS/Vue 正则字面量。前后限定在同一行内，避免把除号当成定界符后吞掉半行。
_JS_LITERAL = re.compile(r"/\^?[^/\n\\]{0,120}(?:\\.[^/\n\\]{0,120}){0,20}/[gimsuy]*")

#: Python 里传给 `re` 的模式串。
_PY_PATTERN = re.compile(
    r"re\.(?:compile|match|search|fullmatch|sub|subn|split|findall|finditer)"
    r"\s*\(\s*(?:r|rb|br)?(['\"])(.*?)\1"
)

#: 丢了反斜杠的字符类简写。`(?<![\\A-Za-z0-9_])` 排除 `\d+`（正确）
#: 与 `id+`、`add+` 这类把字母当字面量的写法。
_LOST_ESCAPE = re.compile(r"(?<![\\A-Za-z0-9_])[dwsbDWSB]\+")

#: 只扫代码，不扫注释——本文件与相关注释里刻意引用了坏形态作为反例。
_LINE_COMMENT = re.compile(r"^\s*(?://|#|\*|/\*)")

_SCAN_ROOTS = (
    ("webui/src", ("*.ts", "*.vue", "*.js")),
    ("kirara_ai", ("*.py",)),
    ("scripts", ("*.py",)),
)


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """行号与代码内容，跳过整行注释。

    只跳整行注释而不做完整的词法分析：行尾注释里出现这种形态的概率极低，
    而为了排除它去实现一个 JS/TS 词法器，会让这条守卫本身成为需要测试的东西。
    """

    lines: list[tuple[int, str]] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        if _LINE_COMMENT.match(line):
            continue
        lines.append((number, line))
    return lines


def _scan(path: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    python = path.suffix == ".py"
    for number, line in _code_lines(path):
        probes = (
            [match.group(2) for match in _PY_PATTERN.finditer(line)]
            if python
            else [match.group(0) for match in _JS_LITERAL.finditer(line)]
        )
        if any(_LOST_ESCAPE.search(probe) for probe in probes):
            findings.append((number, line.strip()[:160]))
    return findings


def test_no_regex_literal_has_lost_its_backslashes():
    findings: list[str] = []
    scanned = 0
    for relative, patterns in _SCAN_ROOTS:
        root = REPOSITORY_ROOT / relative
        if not root.is_dir():
            continue
        for pattern in patterns:
            for path in sorted(root.rglob(pattern)):
                scanned += 1
                for number, line in _scan(path):
                    findings.append(
                        f"{path.relative_to(REPOSITORY_ROOT).as_posix()}:{number}: {line}"
                    )

    assert scanned > 0, "扫描范围为空——目录改名后这条守卫会静默失效"
    assert not findings, (
        "正则字面量里的反斜杠丢了。这类正则合法、不报错，但永远匹配不上：\n"
        + "\n".join(findings)
    )


def test_the_guard_catches_the_shape_it_is_written_for(tmp_path: Path):
    """守卫本身要能抓住它针对的那个形态，否则它只是一条恒真断言。

    用真实缺陷的原文（`ResourceView.vue` 当时那两行）作为样本。
    """

    sample = tmp_path / "sample.ts"
    sample.write_text(
        "const match = /^(d+).(d+).(d+)/.exec(current)\n"
        "if (!/^d+.d+.d+/.test(value)) return 'bad'\n",
        encoding="utf-8",
    )

    assert [number for number, _ in _scan(sample)] == [1, 2]


def test_the_guard_does_not_flag_correct_regexes(tmp_path: Path):
    """正确写法不能被误报，否则这条守卫会被当成噪声关掉。"""

    sample = tmp_path / "ok.ts"
    sample.write_text(
        "const ok = /^\\d+\\.\\d+\\.\\d+/.test(value)\n"
        "const id = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$/\n"
        "const words = /\\w+\\s+\\w+/\n"
        "const division = total / count + other / 2\n"
        "const literal = /add+ress/\n",
        encoding="utf-8",
    )

    assert _scan(sample) == []


def test_the_guard_reads_python_patterns_too(tmp_path: Path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import re\n"
        "broken = re.compile('^d+.d+$')\n"
        "fine = re.compile(r'^\\d+\\.\\d+$')\n",
        encoding="utf-8",
    )

    assert [number for number, _ in _scan(sample)] == [2]
