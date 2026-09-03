"""Census: which tests only grep source text, and which of those can never fail?

Motivation. Several defects in this repo passed their tests because the tests
asserted that a *string* appears in a source file rather than that the code
*behaves*. The clearest case: `authoringError` used `/^d+.d+.d+/` (a lost
backslash), so the "create a prompt from text" form rejected every input --
while its test asserted `expect(viewSource).toContain('正文不能为空')`, which
was true of the broken code too.

A source-grep assertion is not automatically wrong: some contracts genuinely
live in source text (an import list, a route decorator, a placeholder string
that must match a spec). What makes one useless is when the *whole file* only
greps, so no behaviour is ever exercised.

This script classifies, it does not judge. Output is a ranked list for review:

  * grep-only files  -- every assertion reads source text. Highest risk.
  * mixed files      -- some behaviour is exercised too. Usually fine.
  * behavioural      -- no source reading at all.

Run: .venv/Scripts/python.exe scripts/audit_source_grep_tests.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: Names that hold file text in this repo's tests (`const viewSource = read(...)`).
_SOURCE_VAR = re.compile(
    r"(?:const|let|var)\s+(\w*(?:[Ss]ource|Text|Contents?|Sql|Css|Html)\w*)\s*=",
)
_READS_FILE = re.compile(r"readFileSync|read_text\s*\(|\.read_bytes\s*\(")

#: Every `expect(` in the file. The captured text is only the head of the
#: argument -- enough to tell "expect(viewSource)" from "expect(parse(x))".
#:
#: Matching the *whole* argument is what an earlier version got wrong: it
#: required a bare identifier, so `expect(parseRepositoryCoordinate('a/b'))`
#: matched nothing and was not counted at all. Files that call functions
#: heavily then looked like they had only grep assertions.
_JS_ASSERT = re.compile(r"expect\(\s*([A-Za-z_$][\w$]*)")
#: Python: `assert "..." in source` / `assert source.count(...)`
_PY_ASSERT = re.compile(r"^\s*assert\s+(.+)$")


@dataclass
class FileReport:
    path: str
    total_assertions: int = 0
    source_assertions: int = 0
    source_vars: set[str] = field(default_factory=set)
    reads_file: bool = False

    @property
    def grep_only(self) -> bool:
        return (
            self.reads_file
            and self.total_assertions > 0
            and self.source_assertions == self.total_assertions
        )

    @property
    def ratio(self) -> float:
        if self.total_assertions == 0:
            return 0.0
        return self.source_assertions / self.total_assertions


def _display_path(path: Path) -> str:
    """仓库内相对路径；仓库外（测试用的临时文件）退回文件名。

    `relative_to` 对仓库外的路径抛 `ValueError`。这个函数只决定报告里显示什么，
    不该让扫描本身失败——尤其是它自己的护栏测试会拿 tmp_path 里的样本喂它。
    """
    try:
        return str(path.relative_to(REPOSITORY_ROOT)).replace("\\", "/")
    except ValueError:
        return path.name


def _scan_js(path: Path) -> FileReport:
    report = FileReport(path=_display_path(path))
    text = path.read_text(encoding="utf-8", errors="replace")
    report.reads_file = bool(_READS_FILE.search(text))
    report.source_vars = set(_SOURCE_VAR.findall(text))
    for match in _JS_ASSERT.finditer(text):
        report.total_assertions += 1
        if match.group(1) in report.source_vars:
            report.source_assertions += 1
    return report


def _scan_py(path: Path) -> FileReport:
    report = FileReport(path=_display_path(path))
    text = path.read_text(encoding="utf-8", errors="replace")
    report.reads_file = bool(_READS_FILE.search(text))
    report.source_vars = set(_SOURCE_VAR.findall(text)) | {
        name for name in re.findall(r"(\w+)\s*=\s*[^\n]*\.read_text\(", text)
    }
    for line in text.splitlines():
        match = _PY_ASSERT.match(line)
        if not match:
            continue
        expression = match.group(1)
        report.total_assertions += 1
        if any(re.search(rf"\b{re.escape(name)}\b", expression) for name in report.source_vars):
            report.source_assertions += 1
    return report


def main() -> None:
    reports: list[FileReport] = []
    for path in sorted((REPOSITORY_ROOT / "webui" / "tests").glob("*.ts")):
        reports.append(_scan_js(path))
    for path in sorted((REPOSITORY_ROOT / "tests").rglob("test_*.py")):
        reports.append(_scan_py(path))

    grep_only = [item for item in reports if item.grep_only]
    mixed = [
        item
        for item in reports
        if item.reads_file and not item.grep_only and item.source_assertions > 0
    ]

    print(f"scanned {len(reports)} test files\n")
    print(f"== grep-only ({len(grep_only)}) — every assertion reads source text ==")
    for item in sorted(grep_only, key=lambda value: -value.total_assertions):
        print(f"  {item.path}  ({item.total_assertions} assertions)")
    print(f"\n== mixed ({len(mixed)}) — greps plus real behaviour ==")
    for item in sorted(mixed, key=lambda value: -value.ratio):
        print(
            f"  {item.path}  {item.source_assertions}/{item.total_assertions}"
            f" ({item.ratio:.0%} grep)"
        )


if __name__ == "__main__":
    main()
