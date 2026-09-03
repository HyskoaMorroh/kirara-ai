"""源码 grep 型测试必须有一份同题的行为测试兜着。

发现过程：`authoringError` 的两处正则丢了反斜杠（`/^d+.d+.d+/`），整条
「从纯文本创建提示词」在界面上不可用——而它的测试是
`expect(viewSource).toContain('正文不能为空')`，字符串在、行为不在，测试全绿。

普查（`scripts/audit_source_grep_tests.py`）发现这不是孤例：webui 有 21 个文件
**每一条断言都在读源码文本**，其中最大的一个 40 条断言，包括把整行代码当字符串
钉住的 `toContain("if (!row.last_run) return '—'")`。

源码 grep 本身不是错的。有些契约确实只活在源码文本里：

- import 清单（`naive-components-are-imported`）
- 路由装饰器、`data-test` 钩子的存在
- 占位符文案要与需求逐字一致
- 「不该有某段代码」这类否定断言（`not.toContain('draggable')`）

**坏的形态是「整个文件只 grep」**：那时没有任何一行产品代码被执行过，
一次等价重构会让它红，而一次真正的行为退化不会。

这条护栏的判据因此不是「不许 grep」，而是**每个纯 grep 文件都要有一份同题的
行为测试**。配对靠显式表，而不是猜文件名：一个测试该由谁兜底是人的判断，
写在表里可以复核，用命名约定推则会在改名时静默失效。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPOSITORY_ROOT / "scripts" / "audit_source_grep_tests.py"

#: 纯 grep 文件 -> 兜住它那部分行为的测试文件。
#:
#: 键是 `webui/tests` 下的文件名（不含 `.test.ts`），值是同目录下的行为测试。
#: 允许多个文件指向同一份行为测试：`im-qr-countdown` 与 `im-qr-login-status`
#: 测的是同一个 `qrLoginPresentation` 模块的两个面。
_BEHAVIOUR_COMPANION = {
    "resource-staged-imports": "resource-staged-decisions",
    "agent-runtime-settings": "agent-runtime-form",
    "resource-entry-content": "resource-entry-digest",
    "llm-failover-queue-reorder": "llm-failover-order",
    "pricing-display-name": "pricing-form-values",
    "im-qr-countdown": "im-qr-login-logic",
    "im-qr-login-status": "im-qr-login-logic",
    "im-qr-login-refresh": "im-qr-login-logic",
    # 这一页的判断在后端：`resolve_reply_stream_mode` 的三层优先级由
    # `tests/agent_runtime/test_reply_stream_mode_scope.py` 等三份用例覆盖。
    # 前端只是把三个选项摆出来并原样提交，没有可抽的计算。
    "agent-reply-stream-mode": None,
    # 同理：重置熔断是一次带确认的 POST，前端唯一的判断是
    # `row.state !== 'closed'`（后端 `test_circuit_reset.py` 覆盖真实语义）。
    "llm-circuit-reset-control": None,
}


def _audit_module():
    """按路径加载普查脚本。

    先登记进 `sys.modules` 再执行：脚本里的 `@dataclass` 在解析
    `set[str]` 这类字符串注解时会去 `sys.modules[cls.__module__]` 取命名空间，
    未登记时那里是 `None`，于是 dataclasses 抛
    `AttributeError: 'NoneType' object has no attribute '__dict__'`——
    一个与被测内容毫无关系的报错。
    """
    spec = importlib.util.spec_from_file_location("kirara_grep_audit", AUDIT_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audit():
    return _audit_module()


def _grep_only_names(audit) -> set[str]:
    names: set[str] = set()
    for path in sorted((REPOSITORY_ROOT / "webui" / "tests").glob("*.ts")):
        report = audit._scan_js(path)
        if report.grep_only:
            names.add(path.name.removesuffix(".test.ts"))
    return names


def test_every_grep_only_file_is_accounted_for(audit):
    """新增一个纯 grep 文件必须同时决定谁兜底。

    这条会在有人写下一个「只查源码字符串」的测试文件时红，
    并要求他在 `_BEHAVIOUR_COMPANION` 里显式回答一个问题：
    这一页的行为由谁验证？答案可以是「后端已覆盖」（填 `None`），
    但必须写下来。
    """
    unaccounted = _grep_only_names(audit) - set(_BEHAVIOUR_COMPANION)
    assert not unaccounted, (
        "这些测试文件的每一条断言都只读源码文本，没有任何行为被执行。\n"
        "请补一份调用函数的行为测试，并在 _BEHAVIOUR_COMPANION 里登记；\n"
        "若该页逻辑确实在后端，填 None 并写明理由：\n  "
        + "\n  ".join(sorted(unaccounted))
    )


def test_the_registered_companions_exist():
    """登记的兜底文件必须真的存在——否则这张表只是一句声明。"""
    missing: list[str] = []
    for grep_file, companion in _BEHAVIOUR_COMPANION.items():
        if companion is None:
            continue
        if not (REPOSITORY_ROOT / "webui" / "tests" / f"{companion}.test.ts").is_file():
            missing.append(f"{grep_file} -> {companion}")
    assert not missing, "登记的行为测试文件不存在：\n  " + "\n  ".join(missing)


def test_the_companions_actually_import_product_code(audit):
    """兜底文件必须 import 产品模块并调用它，而不是又一份 grep。

    否则这条护栏会被一个「看起来像行为测试」的文件满足，
    而它其实同样只读源码——那正是它要防的东西。
    """
    offenders: list[str] = []
    for companion in {value for value in _BEHAVIOUR_COMPANION.values() if value}:
        path = REPOSITORY_ROOT / "webui" / "tests" / f"{companion}.test.ts"
        text = path.read_text(encoding="utf-8")
        if not re.search(r"from '\.\./src/", text):
            offenders.append(f"{companion}: 没有 import ../src/ 下的模块")
            continue
        report = audit._scan_js(path)
        if report.grep_only:
            offenders.append(f"{companion}: 它自己也是纯 grep")
    assert not offenders, "登记的行为测试不合格：\n  " + "\n  ".join(offenders)


def test_the_registry_has_no_stale_entries(audit):
    """一个文件补上行为断言之后，要从表里摘掉。

    留着过期条目会让这张表越来越像一份免责清单，而不是待办。
    """
    grep_only = _grep_only_names(audit)
    stale = [name for name in _BEHAVIOUR_COMPANION if name not in grep_only]
    assert not stale, (
        "这些文件已经不是纯 grep 了（大概是补了行为断言），请从 "
        "_BEHAVIOUR_COMPANION 里删掉：\n  " + "\n  ".join(sorted(stale))
    )


def test_the_audit_classifier_is_not_vacuous(audit, tmp_path: Path):
    """普查脚本本身要能分辨两种文件，否则上面几条全是恒真断言。"""
    grep_only = tmp_path / "a.test.ts"
    grep_only.write_text(
        # `readFileSync` 是判据之一：没有读文件的证据就不算 grep 型。
        "const viewSource = readFileSync('x')\n"
        "it('x', () => { expect(viewSource).toContain('y') })\n",
        encoding="utf-8",
    )
    behavioural = tmp_path / "b.test.ts"
    behavioural.write_text(
        "import { f } from '../src/f'\n"
        "const viewSource = readFileSync('x')\n"
        "it('x', () => { expect(f(1)).toBe(2); expect(viewSource).toContain('y') })\n",
        encoding="utf-8",
    )

    assert audit._scan_js(grep_only).grep_only is True
    assert audit._scan_js(behavioural).grep_only is False


def test_a_call_expression_counts_as_behaviour(audit, tmp_path: Path):
    """`expect(parse('a/b'))` 必须算作行为断言。

    早期版本的匹配只认裸标识符，于是这类调用一条都没统计上，
    重度调用函数的文件反而被判成「只有 grep 断言」。
    """
    sample = tmp_path / "c.test.ts"
    sample.write_text(
        "import { parse } from '../src/parse'\n"
        "const viewSource = readFileSync('x')\n"
        "it('x', () => { expect(parse('a/b')).toEqual({}) })\n",
        encoding="utf-8",
    )

    report = audit._scan_js(sample)
    assert report.total_assertions == 1
    assert report.source_assertions == 0
